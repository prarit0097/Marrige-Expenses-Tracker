from datetime import datetime, date, timedelta
from dateutil import tz
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.utils import secure_filename
import csv, io, os, pytz

try:
    import psycopg2  # ensure available at runtime if DATABASE_URL is Postgres
except Exception:
    pass

# -------- App Config --------
app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret')

# DB: Use DATABASE_URL (Postgres on Render/Neon) else local SQLite
db_url = os.getenv('DATABASE_URL')
if db_url:
    # Use psycopg (v3) driver with SQLAlchemy on Render/Neon
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif db_url.startswith('postgresql://') and 'postgresql+psycopg://' not in db_url and '+psycopg' not in db_url:
        db_url = db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url

else:
    os.makedirs(app.instance_path, exist_ok=True)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'tracker.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Uploads (note: not persistent on free Render)
UPLOAD_FOLDER = os.path.join(app.instance_path, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'.png','.jpg','.jpeg','.pdf'}

db = SQLAlchemy(app)

# -------- Timezone --------
IST = tz.gettz('Asia/Kolkata')
def now_ist():
    return datetime.now(tz=IST)

# -------- Models --------
class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dt = db.Column(db.DateTime, nullable=False, index=True)  # exact date+time of expense
    category = db.Column(db.String(80), nullable=False, index=True)
    subcategory = db.Column(db.String(80), nullable=True, index=True)
    vendor = db.Column(db.String(120), nullable=True, index=True)
    description = db.Column(db.String(240), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    payment_mode = db.Column(db.String(40), nullable=True)   # Cash, UPI, Card, Bank
    payment_type = db.Column(db.String(20), nullable=True)   # Advance, Final, Other
    notes = db.Column(db.Text, nullable=True)
    attachment = db.Column(db.String(200), nullable=True)    # stored filename
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: now_ist())

    def __repr__(self):
        return f"<Expense {self.id} ₹{self.amount} {self.category}>"

# -------- Defaults --------
DEFAULT_CATEGORIES = [
    ("Venue", ["Hall", "Decor", "Lighting", "Sound"]),
    ("Catering", ["Food", "Beverages", "Snacks", "Desserts"]),
    ("Photography", ["Candid", "Videography", "Album"]),
    ("Attire", ["Bride", "Groom", "Family"]),
    ("Jewellery", ["Gold", "Diamond", "Rental"]),
    ("Invitations", ["Cards", "E-Invites"]),
    ("Gifts", ["Return Gifts", "Guests"]),
    ("Travel", ["Cars", "Logistics"]),
    ("Accommodation", ["Hotel", "Guest House"]),
    ("Rituals", ["Pandit", "Samagri", "Mehendi", "Haldi", "Sagan"]),
    ("Entertainment", ["DJ", "Live Band", "Band-Baja"]),
    ("Baraat", ["Logistics", "Horse/Car", "Dhol"]),
    ("Misc", ["Tips", "Contingency"]),
]

BUDGET_OVERALL = 1000000  # ₹10,00,000

# -------- Helpers & Filters --------
def parse_dt(date_str, time_str):
    if not date_str:
        raise ValueError("Date is required")
    if not time_str:
        time_str = "12:00"
    dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return dt_naive.replace(tzinfo=IST)

def allowed_file(filename):
    return os.path.splitext(filename)[1].lower() in {'.png','.jpg','.jpeg','.pdf'}

@app.template_filter("ist")
def fmt_ist(dt):
    if not dt:
        return ""
    return dt.astimezone(pytz.timezone('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p')

@app.context_processor
def inject_budget():
    total = db.session.query(func.coalesce(func.sum(Expense.amount), 0.0)).scalar() or 0.0
    pct = (total / BUDGET_OVERALL * 100.0) if BUDGET_OVERALL else 0.0
    return dict(total_spent=total, budget=BUDGET_OVERALL, budget_pct=pct)

# -------- Routes --------
@app.route('/')
def dashboard():
    total = db.session.query(func.coalesce(func.sum(Expense.amount), 0.0)).scalar() or 0.0

    today = date.today()
    start_today = datetime.combine(today, datetime.min.time()).replace(tzinfo=IST)
    end_today = datetime.combine(today, datetime.max.time()).replace(tzinfo=IST)

    today_total = db.session.query(func.coalesce(func.sum(Expense.amount), 0.0))\
        .filter(Expense.dt.between(start_today, end_today)).scalar() or 0.0

    per_category = db.session.query(Expense.category, func.sum(Expense.amount))\
        .group_by(Expense.category).order_by(func.sum(Expense.amount).desc()).all()

    latest = Expense.query.order_by(Expense.dt.desc()).limit(10).all()

    start_30 = now_ist() - timedelta(days=30)
    last30 = db.session.query(
        func.strftime('%Y-%m-%d', Expense.dt),
        func.sum(Expense.amount)
    ).filter(Expense.dt >= start_30)\
     .group_by(func.strftime('%Y-%m-%d', Expense.dt))\
     .order_by(func.strftime('%Y-%m-%d', Expense.dt)).all()

    return render_template('index.html', total=total, today_total=today_total,
                           per_category=per_category, latest=latest, last30=last30)

@app.route('/expenses')
def expenses_list():
    q = Expense.query
    start = request.args.get('start')
    end = request.args.get('end')
    category = request.args.get('category')
    search = request.args.get('search')
    payment_type = request.args.get('payment_type')

    if start:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
        q = q.filter(Expense.dt >= start_dt)
    if end:
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST)
        q = q.filter(Expense.dt <= end_dt)
    if category:
        q = q.filter(Expense.category == category)
    if payment_type:
        q = q.filter(Expense.payment_type == payment_type)
    if search:
        like = f"%{search}%"
        q = q.filter((Expense.vendor.ilike(like)) | (Expense.description.ilike(like)) | (Expense.notes.ilike(like)))

    expenses = q.order_by(Expense.dt.desc()).all()
    subtotal = sum(e.amount for e in expenses)
    cats = [c[0] for c in db.session.query(Expense.category).distinct().all()]

    return render_template('expenses_list.html', expenses=expenses, subtotal=subtotal, cats=cats,
                           start=start, end=end, category=category, search=search, payment_type=payment_type)

@app.route('/expenses/new', methods=['GET', 'POST'])
@app.route('/expenses/<int:expense_id>/edit', methods=['GET', 'POST'])
def expense_form(expense_id=None):
    expense = Expense.query.get(expense_id) if expense_id else None

    if request.method == 'POST':
        try:
            dt = parse_dt(request.form['date'], request.form.get('time') or '12:00')
            amount = float(request.form['amount'])
            category = request.form['category']
            subcategory = request.form.get('subcategory') or None
            vendor = request.form.get('vendor')
            description = request.form.get('description')
            payment_mode = request.form.get('payment_mode')
            payment_type = request.form.get('payment_type') or None
            notes = request.form.get('notes')

            if expense:
                expense.dt = dt
                expense.amount = amount
                expense.category = category
                expense.subcategory = subcategory
                expense.vendor = vendor
                expense.description = description
                expense.payment_mode = payment_mode
                expense.payment_type = payment_type
                expense.notes = notes
            else:
                expense = Expense(dt=dt, amount=amount, category=category, subcategory=subcategory,
                                  vendor=vendor, description=description, payment_mode=payment_mode,
                                  payment_type=payment_type, notes=notes)
                db.session.add(expense)

            # Attachment upload
            if 'file' in request.files:
                file = request.files['file']
                if file and file.filename:
                    ext = os.path.splitext(file.filename)[1].lower()
                    if ext not in ALLOWED_EXTENSIONS:
                        raise ValueError("File type not allowed (png, jpg, jpeg, pdf only).")
                    from werkzeug.utils import secure_filename
                    filename = secure_filename(file.filename)
                    # ensure unique
                    base, ext = os.path.splitext(filename)
                    final = filename
                    i = 1
                    while os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], final)):
                        final = f"{base}_{i}{ext}"
                        i += 1
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], final)
                    file.save(filepath)
                    expense.attachment = final

            db.session.commit()
            flash('Saved successfully', 'success')
            return redirect(url_for('expenses_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'error')

    categories = DEFAULT_CATEGORIES

    form_defaults = {}
    if expense:
        form_defaults = {
            'date': expense.dt.astimezone(IST).strftime('%Y-%m-%d'),
            'time': expense.dt.astimezone(IST).strftime('%H:%M'),
            'amount': expense.amount,
            'category': expense.category,
            'subcategory': expense.subcategory or '',
            'vendor': expense.vendor or '',
            'description': expense.description or '',
            'payment_mode': expense.payment_mode or '',
            'payment_type': expense.payment_type or '',
            'notes': expense.notes or '',
        }

    return render_template('expense_form.html', expense=expense, categories=categories, form_defaults=form_defaults)

@app.route('/expenses/<int:expense_id>/delete', methods=['POST'])
def expense_delete(expense_id):
    e = Expense.query.get_or_404(expense_id)
    db.session.delete(e)
    db.session.commit()
    flash('Deleted', 'success')
    return redirect(url_for('expenses_list'))

@app.route('/import-export')
def import_export():
    return render_template('import_export.html')

@app.route('/export.csv')
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['date', 'time', 'category', 'subcategory', 'vendor', 'description', 'amount', 'payment_mode', 'payment_type', 'notes', 'attachment'])
    for e in Expense.query.order_by(Expense.dt.asc()).all():
        d = e.dt.astimezone(IST)
        writer.writerow([
            d.strftime('%Y-%m-%d'), d.strftime('%H:%M'), e.category, e.subcategory or '', e.vendor or '',
            e.description or '', f"{e.amount:.2f}", e.payment_mode or '', e.payment_type or '',
            e.notes or '', e.attachment or ''
        ])
    mem = io.BytesIO(output.getvalue().encode('utf-8'))
    mem.seek(0)
    return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='expenses_all.csv')

@app.route('/import.csv', methods=['POST'])
def import_csv():
    file = request.files.get('file')
    if not file:
        flash('Choose a CSV file to import', 'error')
        return redirect(url_for('import_export'))

    count = 0
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8'))
        reader = csv.DictReader(stream)
        for row in reader:
            dt = parse_dt(row.get('date'), row.get('time'))
            amount = float(row.get('amount'))
            expense = Expense(
                dt=dt,
                category=row.get('category') or 'Misc',
                subcategory=row.get('subcategory') or None,
                vendor=row.get('vendor'),
                description=row.get('description'),
                amount=amount,
                payment_mode=row.get('payment_mode'),
                payment_type=row.get('payment_type'),
                notes=row.get('notes'),
                attachment=row.get('attachment') or None
            )
            db.session.add(expense)
            count += 1
        db.session.commit()
        flash(f'Imported {count} rows', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Import failed: {e}', 'error')
    return redirect(url_for('expenses_list'))

# ---------- Reports & Exports ----------
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

@app.route('/report/vendor')
def report_vendor():
    rows = db.session.query(Expense.vendor, func.sum(Expense.amount))\
        .group_by(Expense.vendor).order_by(func.sum(Expense.amount).desc()).all()
    adv = db.session.query(func.coalesce(func.sum(Expense.amount), 0.0)).filter(Expense.payment_type=='Advance').scalar() or 0.0
    fin = db.session.query(func.coalesce(func.sum(Expense.amount), 0.0)).filter(Expense.payment_type=='Final').scalar() or 0.0
    oth = db.session.query(func.coalesce(func.sum(Expense.amount), 0.0)).filter(Expense.payment_type=='Other').scalar() or 0.0
    return render_template('report_vendor.html', rows=rows, adv=adv, fin=fin, oth=oth)

@app.route('/report/monthly')
def report_monthly():
    rows = db.session.query(
        func.strftime('%Y-%m', Expense.dt),
        func.sum(Expense.amount)
    ).group_by(func.strftime('%Y-%m', Expense.dt))\
     .order_by(func.strftime('%Y-%m', Expense.dt)).all()
    return render_template('report_monthly.html', rows=rows)

@app.route('/export/vendor.csv')
def export_vendor_csv():
    rows = db.session.query(Expense.vendor, func.sum(Expense.amount))\
        .group_by(Expense.vendor).order_by(func.sum(Expense.amount).desc()).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['Vendor', 'Total Amount (₹)'])
    for v, amt in rows:
        w.writerow([v or '—', f"{float(amt):.2f}"])
    mem = io.BytesIO(output.getvalue().encode('utf-8')); mem.seek(0)
    return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='vendor_summary.csv')

@app.route('/export/monthly.csv')
def export_monthly_csv():
    rows = db.session.query(
        func.strftime('%Y-%m', Expense.dt),
        func.sum(Expense.amount)
    ).group_by(func.strftime('%Y-%m', Expense.dt))\
     .order_by(func.strftime('%Y-%m', Expense.dt)).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['Month (YYYY-MM)', 'Total Amount (₹)'])
    for m, amt in rows:
        w.writerow([m, f"{float(amt):.2f}"])
    mem = io.BytesIO(output.getvalue().encode('utf-8')); mem.seek(0)
    return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='monthly_summary.csv')

def _pdf_table(filename, title, headers, rows):
    path = io.BytesIO()
    c = canvas.Canvas(path, pagesize=landscape(A4))
    width, height = landscape(A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, height-2*cm, title)
    c.setFont("Helvetica", 10)
    y = height - 3*cm
    col_widths = [max(6*cm, (width-4*cm)/len(headers))]*len(headers)
    x = 2*cm
    for i,h in enumerate(headers):
        c.drawString(x, y, str(h))
        x += col_widths[i]
    y -= 0.8*cm
    for row in rows:
        if y < 2*cm:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - 2*cm
        x = 2*cm
        for i,cell in enumerate(row):
            c.drawString(x, y, str(cell))
            x += col_widths[i]
        y -= 0.6*cm
    c.showPage()
    c.save()
    path.seek(0)
    return send_file(path, mimetype='application/pdf', as_attachment=True, download_name=filename)

@app.route('/export/vendor.pdf')
def export_vendor_pdf():
    rows = db.session.query(Expense.vendor, func.sum(Expense.amount))\
        .group_by(Expense.vendor).order_by(func.sum(Expense.amount).desc()).all()
    rows_fmt = [(v or '—', f"₹{float(a):.2f}") for v,a in rows]
    return _pdf_table('vendor_summary.pdf', 'Vendor-wise Summary', ['Vendor','Total (₹)'], rows_fmt)

@app.route('/export/monthly.pdf')
def export_monthly_pdf():
    rows = db.session.query(
        func.strftime('%Y-%m', Expense.dt),
        func.sum(Expense.amount)
    ).group_by(func.strftime('%Y-%m', Expense.dt))\
     .order_by(func.strftime('%Y-%m', Expense.dt)).all()
    rows_fmt = [(m, f"₹{float(a):.2f}") for m,a in rows]
    return _pdf_table('monthly_summary.pdf', 'Monthly Summary', ['Month','Total (₹)'], rows_fmt)

# Serve uploaded files
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# CLI
@app.cli.command('init-db')
def init_db():
    with app.app_context():
        db.create_all()
        print("Database initialized at:", app.config['SQLALCHEMY_DATABASE_URI'])

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
