import cv2
import torch
import os
import numpy as np
import pathlib
import platform

if platform.system() == 'Windows':
    pathlib.PosixPath = pathlib.WindowsPath

# ============================================================
#  PATH
#  ใช้ path แบบ relative (อิงจากตำแหน่งไฟล์นี้เอง) แทนการ hardcode
#  path ของเครื่อง Windows เดิม เพื่อให้รันได้ทั้งบน local และบน server
# ============================================================
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_DIR      = os.path.join(BASE_DIR, 'weights')
STICKER_DIR      = os.path.join(BASE_DIR, 'static', 'stickers')

PLATE_MODEL_PATH = os.path.join(WEIGHTS_DIR, 'jubjub.pt')   # YOLOv5 ป้ายทะเบียน
FACE_MODEL_PATH  = os.path.join(WEIGHTS_DIR, 'betty.pt')    # YOLOv8 ใบหน้า (เทรนใหม่)

# ---- threshold ----
CONF_PLATE    = 0.20
CONF_FACE     = 0.45
IOU_THRESHOLD = 0.40
PADDING_PLATE = 0.08
PADDING_FACE  = 0.50   # เพิ่มขึ้นจาก 0.12 → ครอบใบหน้าครบขึ้น

# ============================================================
#  โหลดโมเดล
# ============================================================
print("กำลังโหลดโมเดล...")

try:
    plate_model = torch.hub.load(
        'ultralytics/yolov5', 'custom',
        path=PLATE_MODEL_PATH, force_reload=False
    )
    plate_model.conf     = CONF_PLATE
    plate_model.iou      = IOU_THRESHOLD
    plate_model.agnostic = True
    print(f"✅ โหลดโมเดลป้ายทะเบียนสำเร็จ | classes: {plate_model.names}")
except Exception as e:
    print(f"❌ โหลดโมเดลป้ายทะเบียนไม่สำเร็จ: {e}")
    plate_model = None

try:
    from ultralytics import YOLO
    face_model = YOLO(FACE_MODEL_PATH)
    print(f"✅ โหลดโมเดลใบหน้าสำเร็จ (YOLOv8) | classes: {face_model.names}")
except Exception as e:
    print(f"❌ โหลดโมเดลใบหน้าไม่สำเร็จ: {e}")
    face_model = None


# ============================================================
#  Helper: อ่าน / บันทึกรูป (รองรับ path ภาษาไทย)
# ============================================================
def read_image(path: str):
    try:
        arr = np.fromfile(path, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            print(f"❌ imdecode คืนค่า None: {path}")
        return img
    except Exception as e:
        print(f"❌ อ่านรูปไม่ได้: {e}")
        return None


def save_image(path: str, img: np.ndarray) -> bool:
    try:
        ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if ok:
            buf.tofile(path)
            return True
        return False
    except Exception as e:
        print(f"❌ บันทึกรูปไม่ได้: {e}")
        return False


def add_padding(x1, y1, x2, y2, img_h, img_w, ratio=0.08):
    pw = int((x2 - x1) * ratio)
    ph = int((y2 - y1) * ratio)
    return (
        max(0, x1 - pw),
        max(0, y1 - ph),
        min(img_w, x2 + pw),
        min(img_h, y2 + ph)
    )


# ============================================================
#  เอฟเฟกต์
# ============================================================
def apply_blur(img, x1, y1, x2, y2):
    roi = img[y1:y2, x1:x2]
    h, w = roi.shape[:2]
    ksize = max(151, int(max(h, w) * 0.9) | 1)  # เพิ่มจาก 0.6 → 0.9
    blurred = cv2.GaussianBlur(roi, (ksize, ksize), 0)
    for _ in range(4):  # เพิ่มจาก 2 → 4 รอบ
        blurred = cv2.GaussianBlur(blurred, (ksize, ksize), 0)
    img[y1:y2, x1:x2] = blurred
    return img


def apply_pixelate(img, x1, y1, x2, y2):
    """Pixelate — คำนวณ block จากขนาด ROI จริง ไม่ใช้ค่าคงที่"""
    roi = img[y1:y2, x1:x2]
    h, w = roi.shape[:2]
    if h < 1 or w < 1:
        return img
    # บีบเหลือแค่ 8x8 pixel เสมอ ไม่ว่าหน้าจะใหญ่แค่ไหน
    bw = max(1, min(8, w))
    bh = max(1, min(8, h))
    small = cv2.resize(roi, (bw, bh), interpolation=cv2.INTER_LINEAR)
    img[y1:y2, x1:x2] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    return img


def apply_mosaic(img, x1, y1, x2, y2):
    """Mosaic — block ใหญ่หยาบ ดูต่างจาก pixelate ชัดเจน"""
    roi = img[y1:y2, x1:x2]
    h, w = roi.shape[:2]
    if h < 1 or w < 1:
        return img
    # block ใหญ่มาก = สี่เหลี่ยมหยาบๆ
    block = max(16, min(w, h) // 3)
    for y in range(0, h, block):
        for x in range(0, w, block):
            y2b = min(y + block, h)
            x2b = min(x + block, w)
            tile  = roi[y:y2b, x:x2b]
            color = tile.mean(axis=(0, 1)).astype(int)
            roi[y:y2b, x:x2b] = color
    img[y1:y2, x1:x2] = roi
    return img


def apply_sticker(img, x1, y1, x2, y2, sticker_img):
    if sticker_img is None:
        return apply_pixelate(img, x1, y1, x2, y2)
    h, w = y2 - y1, x2 - x1
    if h < 1 or w < 1:
        return img
    resized = cv2.resize(sticker_img, (w, h))
    if len(resized.shape) == 3 and resized.shape[2] == 4:
        alpha   = resized[:, :, 3:4] / 255.0
        resized = resized[:, :, :3]
        img[y1:y2, x1:x2] = (resized * alpha + img[y1:y2, x1:x2] * (1 - alpha)).astype(np.uint8)
    else:
        img[y1:y2, x1:x2] = resized
    return img


def apply_effect(img, x1, y1, x2, y2, effect, sticker_img=None):
    if effect == 'blur':
        return apply_blur(img, x1, y1, x2, y2)
    elif effect == 'pixelate':
        return apply_pixelate(img, x1, y1, x2, y2)
    elif effect == 'mosaic':
        return apply_mosaic(img, x1, y1, x2, y2)
    elif effect == 'sticker':
        return apply_sticker(img, x1, y1, x2, y2, sticker_img)
    return img


# ============================================================
#  Detection
# ============================================================
def detect_plate(img, effect, sticker_img):
    if plate_model is None:
        print("⚠️ ข้าม: plate_model ไม่พร้อม")
        return img, 0
    img_h, img_w = img.shape[:2]
    results    = plate_model(img)
    detections = results.xyxy[0]
    print(f"   [jubjub] พบ {len(detections)} detection")
    applied = 0
    for det in detections:
        x1, y1, x2, y2 = int(det[0]), int(det[1]), int(det[2]), int(det[3])
        conf = float(det[4])
        if conf < CONF_PLATE:
            continue
        x1, y1, x2, y2 = add_padding(x1, y1, x2, y2, img_h, img_w, PADDING_PLATE)
        print(f"      ✔ plate conf={conf:.2f} → {effect}")
        img = apply_effect(img, x1, y1, x2, y2, effect, sticker_img)
        applied += 1
    return img, applied


def detect_face(img, effect, sticker_img):
    if face_model is None:
        print("⚠️ ข้าม: face_model ไม่พร้อม")
        return img, 0
    img_h, img_w = img.shape[:2]
    results = face_model.predict(img, conf=CONF_FACE, iou=IOU_THRESHOLD, verbose=False)
    applied = 0
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        print(f"   [betty] พบ {len(boxes)} detection")
        for box in boxes:
            conf = float(box.conf[0])
            if conf < CONF_FACE:
                continue
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            x1, y1, x2, y2 = add_padding(x1, y1, x2, y2, img_h, img_w, PADDING_FACE)
            print(f"      ✔ face conf={conf:.2f} → {effect}")
            img = apply_effect(img, x1, y1, x2, y2, effect, sticker_img)
            applied += 1
    return img, applied


# ============================================================
#  ฟังก์ชันหลัก
# ============================================================
def process_image(img_path: str, effect: str, targets: list,
                  sticker_name: str = 'censor_black') -> str:
    img = read_image(img_path)
    if img is None:
        return img_path

    img_h, img_w = img.shape[:2]
    print(f"\n{'='*55}")
    print(f"🖼️  {os.path.basename(img_path)} ({img_w}x{img_h})")
    print(f"   targets={targets} | effect={effect}")
    print(f"{'='*55}")

    # โหลด sticker
    sticker_img = None
    if effect == 'sticker':
        for ext in ['.png', '.jpg', '.jpeg']:
            sp = os.path.join(STICKER_DIR, sticker_name + ext)
            if os.path.exists(sp):
                sticker_img = read_image(sp)
                break
        if sticker_img is None:
            print(f"⚠️ หา sticker '{sticker_name}' ไม่เจอ → pixelate แทน")
            effect = 'pixelate'

    total = 0

    want_plate = any('plate' in t.lower() or 'licens' in t.lower() or 'ทะเบียน' in t for t in targets)
    want_face  = any('face' in t.lower() or 'ใบหน้า' in t for t in targets)

    if want_plate:
        print(f"\n🚗 ป้ายทะเบียน — jubjub.pt (YOLOv5)")
        img, n = detect_plate(img, effect, sticker_img)
        total += n
        print(f"   → เบลอ {n} จุด")

    if want_face:
        print(f"\n👤 ใบหน้า — betty.pt (YOLOv8)")
        img, n = detect_face(img, effect, sticker_img)
        total += n
        print(f"   → เบลอ {n} จุด")

    print(f"\n✅ รวม: {total} จุด")

    filename       = os.path.basename(img_path)
    processed_path = os.path.join('static', 'processed', filename)
    os.makedirs(os.path.dirname(processed_path), exist_ok=True)
    save_image(processed_path, img)
    print(f"💾 บันทึกแล้วที่: {processed_path}")

    return processed_path