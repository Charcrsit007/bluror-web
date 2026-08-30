import os
import re
import json
import bcrypt
import mysql.connector
from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv

from image_processor import process_image

# โหลดค่าจากไฟล์ .env (local dev) — บน production platform (Railway/Render)
# ค่าพวกนี้จะถูกตั้งเป็น Environment Variables ในหน้า dashboard แทน ไม่ต้องมีไฟล์ .env
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError("ไม่พบ SECRET_KEY — ตั้งค่าใน .env หรือ environment variables ก่อนรัน")

app.config['UPLOAD_FOLDER']    = 'static/uploads'
app.config['PROCESSED_FOLDER'] = 'static/processed'
os.makedirs(app.config['UPLOAD_FOLDER'],    exist_ok=True)
os.makedirs(app.config['PROCESSED_FOLDER'], exist_ok=True)


def get_db_connection():
    return mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD'),
        database=os.environ.get('DB_NAME', 'blur_db'),
        port=int(os.environ.get('DB_PORT', 3306)),
    )

def admin_required():
    return 'user_id' in session and session.get('role') == 'admin'

def is_valid_username(u):
    if len(u) < 3: return False, "ชื่อผู้ใช้ต้องมีอย่างน้อย 3 ตัวอักษร"
    if len(u) > 30: return False, "ชื่อผู้ใช้ต้องไม่เกิน 30 ตัวอักษร"
    if not re.match(r'^[a-zA-Z0-9_-]+$', u): return False, "ใช้ได้เฉพาะ a-z A-Z 0-9 _ -"
    return True, ""

def is_valid_email(e):
    if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', e):
        return False, "รูปแบบ email ไม่ถูกต้อง"
    return True, ""

def is_strong_password(pw):
    if len(pw) < 8: return False, "รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร"
    if not re.search(r'[A-Z]', pw): return False, "ต้องมีตัวพิมพ์ใหญ่ (A-Z)"
    if not re.search(r'[a-z]', pw): return False, "ต้องมีตัวพิมพ์เล็ก (a-z)"
    if not re.search(r'\d',    pw): return False, "ต้องมีตัวเลข (0-9)"
    if not re.search(r'[!@#$%^&*(),.?\":{}|<>]', pw): return False, "ต้องมีอักขระพิเศษ"
    return True, ""


# ============================================================
#  Auth
# ============================================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        full_name  = request.form.get('full_name','').strip()
        username   = request.form.get('username','').strip()
        email      = request.form.get('email','').strip().lower()
        password   = request.form.get('password','')
        confirm_pw = request.form.get('confirm_password','')

        if len(full_name) < 2:
            return render_template('register.html', error="กรุณากรอกชื่อ-นามสกุลให้ครบ")
        ok, msg = is_valid_username(username)
        if not ok: return render_template('register.html', error=msg)
        ok, msg = is_valid_email(email)
        if not ok: return render_template('register.html', error=msg)
        if password != confirm_pw:
            return render_template('register.html', error="รหัสผ่านไม่ตรงกัน")
        ok, msg = is_strong_password(password)
        if not ok: return render_template('register.html', error=msg)

        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE username=%s', (username,))
        if cursor.fetchone():
            cursor.close(); conn.close()
            return render_template('register.html', error="ชื่อผู้ใช้นี้มีคนใช้แล้ว")
        cursor.execute('SELECT id FROM users WHERE email=%s', (email,))
        if cursor.fetchone():
            cursor.close(); conn.close()
            return render_template('register.html', error="อีเมลนี้ถูกใช้งานแล้ว")

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cursor.execute(
            'INSERT INTO users (full_name,username,email,password,role) VALUES (%s,%s,%s,%s,%s)',
            (full_name, username, email, hashed, 'user')
        )
        conn.commit(); cursor.close(); conn.close()
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE username=%s', (username,))
        user = cursor.fetchone(); cursor.close(); conn.close()
        if not user:
            return render_template('login.html', error="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
        pw = user['password']
        try:
            match = bcrypt.checkpw(password.encode(), pw.encode()) if pw.startswith('$2') else (password == pw)
        except: match = False
        if not match:
            return render_template('login.html', error="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
        session['user_id']  = user['id']
        session['username'] = user['username']
        session['role']     = user['role']
        return redirect(url_for('admin_dashboard') if user['role'] == 'admin' else url_for('dashboard'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ============================================================
#  User Pages
# ============================================================
@app.route('/')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT avatar FROM users WHERE id=%s', (session['user_id'],))
    u = cursor.fetchone(); cursor.close(); conn.close()
    return render_template('upload.html', username=session['username'], avatar=u['avatar'] if u else None)


@app.route('/upload', methods=['POST'])
def upload():
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'file' not in request.files: return "ไม่มีไฟล์อัปโหลด"
    file = request.files['file']
    if file.filename == '': return "ไม่ได้เลือกไฟล์"
    targets = request.form.getlist('target')
    effect  = request.form.get('effect', 'pixelate')
    sticker = request.form.get('sticker', 'censor_black')
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filepath)
    processed_filepath = process_image(filepath, effect, targets, sticker)
    db_orig = filepath.replace('\\', '/')
    db_proc = processed_filepath.replace('\\', '/')
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO images (user_id,original_path,processed_path,blur_type) VALUES (%s,%s,%s,%s)',
        (session['user_id'], db_orig, db_proc, effect)
    )
    conn.commit(); cursor.close(); conn.close()
    return render_template('result.html', original_path=db_orig, processed_path=db_proc, effect=effect)


@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM images WHERE user_id=%s ORDER BY created_at DESC', (session['user_id'],))
    user_images = cursor.fetchall()
    cursor.execute(
        "SELECT target_id,is_like FROM interactions WHERE user_id=%s AND target_type='image'",
        (session['user_id'],)
    )
    user_votes = {r['target_id']: ('like' if r['is_like'] else 'dislike') for r in cursor.fetchall()}
    cursor.close(); conn.close()
    return render_template('gallery.html', images=user_images, user_votes=user_votes)


@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)

    def _get_user():
        cursor.execute('SELECT * FROM users WHERE id=%s', (session['user_id'],))
        return cursor.fetchone()

    if request.method == 'POST':
        action = request.form.get('action', 'password')

        if action == 'avatar':
            if 'avatar' in request.files and request.files['avatar'].filename != '':
                f = request.files['avatar']
                ext = os.path.splitext(f.filename)[1].lower()
                if ext not in ['.jpg','.jpeg','.png','.webp']:
                    u = _get_user(); cursor.close(); conn.close()
                    return render_template('profile.html', user=u, error='รองรับแค่ JPG PNG WEBP')
                avatar_dir = os.path.join('static','avatars'); os.makedirs(avatar_dir, exist_ok=True)
                sp = os.path.join(avatar_dir, f'avatar_{session["user_id"]}{ext}')
                f.save(sp); av = sp.replace('\\','/')
                cursor.execute('UPDATE users SET avatar=%s WHERE id=%s', (av, session['user_id']))
                conn.commit(); session['avatar'] = av
                success_msg = 'อัปเดตรูปโปรไฟล์เรียบร้อย!'
            else:
                success_msg = 'ไม่พบไฟล์รูปภาพ'

        elif action == 'info':
            full_name    = request.form.get('full_name','').strip()
            new_username = request.form.get('new_username','').strip()
            email        = request.form.get('email','').strip().lower()
            if len(full_name) < 2:
                u = _get_user(); cursor.close(); conn.close()
                return render_template('profile.html', user=u, error='กรุณากรอกชื่อ-นามสกุลให้ครบ')
            ok, msg = is_valid_username(new_username)
            if not ok:
                u = _get_user(); cursor.close(); conn.close()
                return render_template('profile.html', user=u, error=msg)
            ok, msg = is_valid_email(email)
            if not ok:
                u = _get_user(); cursor.close(); conn.close()
                return render_template('profile.html', user=u, error=msg)
            cursor.execute('SELECT id FROM users WHERE username=%s AND id!=%s', (new_username, session['user_id']))
            if cursor.fetchone():
                u = _get_user(); cursor.close(); conn.close()
                return render_template('profile.html', user=u, error='ชื่อผู้ใช้นี้มีคนใช้แล้ว')
            cursor.execute('SELECT id FROM users WHERE email=%s AND id!=%s', (email, session['user_id']))
            if cursor.fetchone():
                u = _get_user(); cursor.close(); conn.close()
                return render_template('profile.html', user=u, error='อีเมลนี้ถูกใช้งานแล้ว')
            cursor.execute('UPDATE users SET full_name=%s,username=%s,email=%s WHERE id=%s',
                           (full_name, new_username, email, session['user_id']))
            conn.commit(); session['username'] = new_username
            success_msg = 'อัปเดตข้อมูลส่วนตัวเรียบร้อย!'

        else:
            new_pw  = request.form.get('new_password','')
            conf_pw = request.form.get('confirm_new_password','')
            if new_pw != conf_pw:
                u = _get_user(); cursor.close(); conn.close()
                return render_template('profile.html', user=u, error='รหัสผ่านไม่ตรงกัน')
            ok, msg = is_strong_password(new_pw)
            if not ok:
                u = _get_user(); cursor.close(); conn.close()
                return render_template('profile.html', user=u, error=msg)
            hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
            cursor.execute('UPDATE users SET password=%s WHERE id=%s', (hashed, session['user_id']))
            conn.commit(); success_msg = 'เปลี่ยนรหัสผ่านสำเร็จแล้ว!'

        u = _get_user(); cursor.close(); conn.close()
        return render_template('profile.html', user=u, success=success_msg)

    u = _get_user(); cursor.close(); conn.close()
    return render_template('profile.html', user=u)


@app.route('/delete_account', methods=['POST'])
def delete_account():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE id=%s', (session['user_id'],))
    conn.commit(); cursor.close(); conn.close()
    session.clear()
    return redirect(url_for('register'))


@app.route('/delete_image/<int:image_id>', methods=['POST'])
def delete_image(image_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM images WHERE id=%s AND user_id=%s', (image_id, session['user_id']))
    img = cursor.fetchone()
    if img:
        for p in [img['original_path'], img['processed_path']]:
            if p and os.path.exists(p):
                try: os.remove(p)
                except: pass
        cursor.execute("DELETE FROM interactions WHERE target_type='image' AND target_id=%s", (image_id,))
        cursor.execute('DELETE FROM images WHERE id=%s', (image_id,))
        conn.commit()
    cursor.close(); conn.close()
    return redirect(url_for('history'))


# ============================================================
#  Admin Dashboard
# ============================================================
@app.route('/admin')
def admin_dashboard():
    if not admin_required(): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)

    cursor.execute('''
        SELECT u.id, u.full_name, u.username, u.email, u.role, u.created_at, u.avatar,
               COUNT(i.id) AS image_count
        FROM users u LEFT JOIN images i ON i.user_id = u.id
        GROUP BY u.id ORDER BY u.id ASC
    ''')
    all_users = cursor.fetchall()

    cursor.execute('''
        SELECT images.*, users.username, users.full_name
        FROM images JOIN users ON images.user_id = users.id
        ORDER BY images.created_at DESC
    ''')
    all_images = cursor.fetchall()

    # top 5 active users
    cursor.execute('''
        SELECT u.username, u.full_name, COUNT(i.id) AS cnt
        FROM users u JOIN images i ON i.user_id = u.id
        GROUP BY u.id ORDER BY cnt DESC LIMIT 5
    ''')
    top_users = cursor.fetchall()

    # avg images per user
    cursor.execute('SELECT COUNT(*) AS total FROM images')
    total_images = cursor.fetchone()['total']
    total_users  = len(all_users)
    avg_img = round(total_images / total_users, 1) if total_users else 0

    def build_daily(rows, key='day'):
        data_map = {str(r[key]): r['count'] for r in rows}
        labels, values = [], []
        for i in range(6, -1, -1):
            d = date.today() - timedelta(days=i)
            labels.append(d.strftime('%d/%m'))
            values.append(data_map.get(str(d), 0))
        return labels, values

    cursor.execute('''SELECT DATE(created_at) AS day, COUNT(*) AS count FROM images
        WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
        GROUP BY DATE(created_at) ORDER BY day ASC''')
    daily_labels, daily_values = build_daily(cursor.fetchall())

    cursor.execute('''SELECT YEAR(created_at) AS yr, WEEK(created_at,1) AS wk,
        MIN(DATE(created_at)) AS week_start, COUNT(*) AS count FROM images
        WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 7 WEEK)
        GROUP BY YEAR(created_at),WEEK(created_at,1) ORDER BY yr,wk''')
    rows_w = cursor.fetchall()

    cursor.execute('''SELECT DATE_FORMAT(created_at,'%Y-%m') AS month, COUNT(*) AS count FROM images
        WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 5 MONTH)
        GROUP BY DATE_FORMAT(created_at,'%Y-%m') ORDER BY month''')
    rows_m = cursor.fetchall()

    cursor.execute('SELECT blur_type, COUNT(*) AS count FROM images GROUP BY blur_type')
    effect_map = {r['blur_type']: r['count'] for r in cursor.fetchall()}

    cursor.execute('''SELECT DATE(created_at) AS day, COUNT(*) AS count FROM users
        WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
        GROUP BY DATE(created_at) ORDER BY day''')
    nu_labels, nu_values = build_daily(cursor.fetchall())

    cursor.close(); conn.close()

    stats = {
        'daily':     {'labels': daily_labels, 'values': daily_values},
        'weekly':    {'labels': [str(r['week_start']) for r in rows_w], 'values': [r['count'] for r in rows_w]},
        'monthly':   {'labels': [r['month'] for r in rows_m], 'values': [r['count'] for r in rows_m]},
        'new_users': {'labels': nu_labels, 'values': nu_values},
        'effects':   {
            'blur':     effect_map.get('blur',0) + effect_map.get('mosaic',0),
            'pixelate': effect_map.get('pixelate',0),
            'sticker':  effect_map.get('sticker',0),
        }
    }

    return render_template('admin.html',
        users=all_users, images=all_images,
        top_users=top_users, avg_img=avg_img,
        stats_json=json.dumps(stats, default=str)
    )


# ---- เปลี่ยน role user ----
@app.route('/admin/user/role/<int:user_id>', methods=['POST'])
def admin_change_role(user_id):
    if not admin_required(): return redirect(url_for('dashboard'))
    if user_id == session['user_id']:
        return redirect(url_for('admin_dashboard'))   # ห้ามเปลี่ยน role ตัวเอง
    new_role = request.form.get('role')
    if new_role not in ('user', 'admin'): return redirect(url_for('admin_dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE users SET role=%s WHERE id=%s', (new_role, user_id))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('admin_dashboard') + '#users')


# ---- ลบ user (admin) ----
@app.route('/admin/user/delete/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if not admin_required(): return redirect(url_for('dashboard'))
    if user_id == session['user_id']: return redirect(url_for('admin_dashboard'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    # ลบไฟล์รูปของ user นั้นทั้งหมด
    cursor.execute('SELECT original_path,processed_path FROM images WHERE user_id=%s', (user_id,))
    for img in cursor.fetchall():
        for p in [img['original_path'], img['processed_path']]:
            if p and os.path.exists(p):
                try: os.remove(p)
                except: pass
    cursor.execute('DELETE FROM interactions WHERE user_id=%s', (user_id,))
    cursor.execute('DELETE FROM images WHERE user_id=%s', (user_id,))
    cursor.execute('DELETE FROM users WHERE id=%s', (user_id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('admin_dashboard') + '#users')


# ---- ลบรูป (admin) ----
@app.route('/admin/image/delete/<int:image_id>', methods=['POST'])
def admin_delete_image(image_id):
    if not admin_required(): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM images WHERE id=%s', (image_id,))
    img = cursor.fetchone()
    if img:
        for p in [img['original_path'], img['processed_path']]:
            if p and os.path.exists(p):
                try: os.remove(p)
                except: pass
        cursor.execute("DELETE FROM interactions WHERE target_type='image' AND target_id=%s", (image_id,))
        cursor.execute('DELETE FROM images WHERE id=%s', (image_id,))
        conn.commit()
    cursor.close(); conn.close()
    return redirect(url_for('admin_dashboard') + '#images')


# ============================================================
#  Knowledge
# ============================================================
@app.route('/knowledge')
def knowledge_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM knowledge ORDER BY id ASC")
    articles = cursor.fetchall()
    cursor.execute(
        "SELECT target_id,is_like FROM interactions WHERE user_id=%s AND target_type='knowledge'",
        (session['user_id'],)
    )
    user_votes = {r['target_id']: ('like' if r['is_like'] else 'dislike') for r in cursor.fetchall()}
    cursor.close(); conn.close()
    return render_template('knowledge.html', articles=articles, user_votes=user_votes)


@app.route('/action/<target_type>/<int:target_id>/<action_type>')
def handle_action(target_type, target_id, action_type):
    if 'user_id' not in session: return redirect(url_for('login'))
    if target_type not in ('image','knowledge'): return redirect(url_for('dashboard'))
    if action_type not in ('like','dislike'): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    table   = 'images' if target_type == 'image' else 'knowledge'
    is_like = 1 if action_type == 'like' else 0
    cursor.execute(
        "SELECT is_like FROM interactions WHERE user_id=%s AND target_type=%s AND target_id=%s",
        (session['user_id'], target_type, target_id)
    )
    existing = cursor.fetchone()
    if existing is None:
        cursor.execute("INSERT INTO interactions (user_id,target_type,target_id,is_like) VALUES (%s,%s,%s,%s)",
                       (session['user_id'], target_type, target_id, is_like))
        col = 'likes' if is_like else 'dislikes'
        cursor.execute(f"UPDATE {table} SET {col}={col}+1 WHERE id=%s", (target_id,))
    elif existing['is_like'] == is_like:
        cursor.execute("DELETE FROM interactions WHERE user_id=%s AND target_type=%s AND target_id=%s",
                       (session['user_id'], target_type, target_id))
        col = 'likes' if is_like else 'dislikes'
        cursor.execute(f"UPDATE {table} SET {col}=GREATEST({col}-1,0) WHERE id=%s", (target_id,))
    else:
        oc = 'likes' if existing['is_like'] else 'dislikes'
        nc = 'likes' if is_like else 'dislikes'
        cursor.execute("UPDATE interactions SET is_like=%s WHERE user_id=%s AND target_type=%s AND target_id=%s",
                       (is_like, session['user_id'], target_type, target_id))
        cursor.execute(f"UPDATE {table} SET {oc}=GREATEST({oc}-1,0),{nc}={nc}+1 WHERE id=%s", (target_id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('history') if target_type == 'image' else url_for('knowledge_page'))


@app.route('/admin/knowledge')
def admin_knowledge():
    if not admin_required(): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM knowledge ORDER BY id DESC")
    articles = cursor.fetchall(); cursor.close(); conn.close()
    return render_template('admin_knowledge.html', articles=articles)

@app.route('/admin/knowledge/add', methods=['POST'])
def add_knowledge():
    if not admin_required(): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute('INSERT INTO knowledge (title,content,category) VALUES (%s,%s,%s)',
                   (request.form['title'], request.form['content'], request.form['category']))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('admin_knowledge'))

@app.route('/admin/knowledge/edit/<int:id>', methods=['POST'])
def edit_knowledge(id):
    if not admin_required(): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute('UPDATE knowledge SET title=%s,content=%s,category=%s WHERE id=%s',
                   (request.form['title'], request.form['content'], request.form['category'], id))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('admin_knowledge'))

@app.route('/admin/knowledge/delete/<int:id>')
def delete_knowledge(id):
    if not admin_required(): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute('DELETE FROM knowledge WHERE id=%s', (id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('admin_knowledge'))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    # ngrok ใช้เฉพาะตอนทดสอบบนเครื่องตัวเอง (local dev tunnel)
    # บน production (Railway/Render) ไม่ต้องใช้ ngrok — แพลตฟอร์มให้ URL สาธารณะมาเอง
    if os.environ.get('USE_NGROK', 'false').lower() == 'true':
        from pyngrok import ngrok, conf
        ngrok_token  = os.environ.get('NGROK_AUTH_TOKEN')
        ngrok_domain = os.environ.get('NGROK_DOMAIN')  # เช่น 'your-name.ngrok-free.dev' (ถ้ามี static domain)
        if not ngrok_token:
            raise RuntimeError("ตั้งค่า NGROK_AUTH_TOKEN ใน .env ก่อนใช้ USE_NGROK=true")
        conf.get_default().auth_token = ngrok_token
        ngrok.kill()
        connect_kwargs = {'domain': ngrok_domain} if ngrok_domain else {}
        public_url = ngrok.connect(port, **connect_kwargs)
        print(f'\n🌐 URL: {public_url}')

    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False)