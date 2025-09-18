from flask import Flask, render_template, request, redirect, url_for, send_file, flash, abort # type: ignore
from flask_sqlalchemy import SQLAlchemy # type: ignore
from io import BytesIO
import pandas as pd
from datetime import datetime, timedelta
import pytz
import os
from werkzeug.utils import secure_filename # type: ignore

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cambia_esto_para_produccion'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ventas.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Zona local
LOCAL_TZ = pytz.timezone('America/Bogota')

class Venta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(120), nullable=False)
    producto = db.Column(db.String(120), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Float, nullable=False)
    metodo_pago = db.Column(db.String(50), nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)  # UTC naive
    pagado = db.Column(db.Boolean, default=False)
    enviado = db.Column(db.Boolean, default=False)
    pagado_fecha = db.Column(db.DateTime, nullable=True)
    enviado_fecha = db.Column(db.DateTime, nullable=True)
    comprobante_path = db.Column(db.String(255), nullable=True)

    @property
    def total(self):
        return round(self.cantidad * self.precio_unitario, 2)

# -------------------
# Jinja filter para mostrar datetimes en zona local
# -------------------
@app.template_filter('local_dt')
def local_dt(dt, fmt='%Y-%m-%d %H:%M:%S'):
    if not dt:
        return '—'
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    return dt.astimezone(LOCAL_TZ).strftime(fmt)

# -------------------
# Helpers
# -------------------
def parse_date_from_str(s, end_of_day=False):
    """Parsea YYYY-MM-DD -> datetime UTC naive."""
    if not s:
        return None
    try:
        d = datetime.strptime(s, '%Y-%m-%d')
    except Exception:
        return None
    if end_of_day:
        d = d + timedelta(hours=23, minutes=59, seconds=59)
    local_dt = LOCAL_TZ.localize(d)
    utc_dt = local_dt.astimezone(pytz.UTC)
    return utc_dt.replace(tzinfo=None)

def apply_filters(query):
    args = request.args
    if args.get('unpaid') == '1':
        query = query.filter(Venta.pagado.is_(False))
    if args.get('unsent') == '1':
        query = query.filter(Venta.enviado.is_(False))

    cliente = args.get('cliente', '').strip()
    if cliente:
        query = query.filter(Venta.cliente.ilike(f'%{cliente}%'))
    producto = args.get('producto', '').strip()
    if producto:
        query = query.filter(Venta.producto.ilike(f'%{producto}%'))
    metodo = args.get('metodo_pago', '').strip()
    if metodo:
        query = query.filter(Venta.metodo_pago.ilike(f'%{metodo}%'))

    date_from = parse_date_from_str(args.get('date_from'))
    date_to = parse_date_from_str(args.get('date_to'), end_of_day=True)
    if date_from:
        query = query.filter(Venta.fecha >= date_from)
    if date_to:
        query = query.filter(Venta.fecha <= date_to)

    return query

def fmt_avg_seconds(sec):
    if sec is None:
        return '—'
    days = int(sec // 86400)
    rem = int(sec % 86400)
    hours = rem // 3600
    mins = (rem % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"

# -------------------
# Rutas
# -------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            cliente = request.form['cliente'].strip()
            producto = request.form['producto'].strip()
            cantidad = int(request.form['cantidad'])
            precio = float(request.form['precio_unitario'])
            metodo = request.form['metodo_pago'].strip()

            if not cliente or not producto or cantidad <= 0 or precio <= 0 or not metodo:
                raise ValueError("Campos inválidos")

            venta = Venta(cliente=cliente, producto=producto, cantidad=cantidad,
                        precio_unitario=precio, metodo_pago=metodo)
            db.session.add(venta)
            db.session.commit()
            flash("Venta registrada.", "success")
            qs = request.query_string.decode('utf-8')
            return redirect(url_for('index') + ('?' + qs if qs else ''))
        except Exception as e:
            flash(f"Error al registrar: {e}", "danger")
            qs = request.query_string.decode('utf-8')
            return redirect(url_for('index') + ('?' + qs if qs else ''))

    # GET
    q = Venta.query
    q = apply_filters(q)
    ventas = q.order_by(Venta.fecha.desc()).all()
    total_count = len(ventas)

    # KPIs
    ingresos_por_venta = [v.total for v in ventas]
    total_ingresos = sum(ingresos_por_venta) if ingresos_por_venta else 0.0
    aov = (total_ingresos / total_count) if total_count > 0 else 0.0

    unpaid_count = sum(1 for v in ventas if not v.pagado)
    unsent_count = sum(1 for v in ventas if not v.enviado)
    pct_unpaid = (unpaid_count / total_count * 100) if total_count > 0 else 0.0
    pct_unsent = (unsent_count / total_count * 100) if total_count > 0 else 0.0

    paid_deltas = []
    for v in ventas:
        if v.pagado and v.pagado_fecha:
            start = pytz.UTC.localize(v.fecha) if v.fecha.tzinfo is None else v.fecha
            end = pytz.UTC.localize(v.pagado_fecha) if v.pagado_fecha.tzinfo is None else v.pagado_fecha
            paid_deltas.append((end - start).total_seconds())
    avg_paid_seconds = sum(paid_deltas) / len(paid_deltas) if paid_deltas else None
    avg_time_to_payment = fmt_avg_seconds(avg_paid_seconds)

    prod_rev = {}
    for v in ventas:
        prod_rev[v.producto] = prod_rev.get(v.producto, 0.0) + v.total
    top_products = sorted(prod_rev.items(), key=lambda x: x[1], reverse=True)[:5]

    today_local = datetime.utcnow().replace(tzinfo=pytz.UTC).astimezone(LOCAL_TZ).date()
    last7 = [(today_local - timedelta(days=i)) for i in range(6, -1, -1)]
    sales_by_day = {d: 0 for d in last7}
    for v in ventas:
        v_local_date = (pytz.UTC.localize(v.fecha) if v.fecha.tzinfo is None else v.fecha).astimezone(LOCAL_TZ).date()
        if v_local_date in sales_by_day:
            sales_by_day[v_local_date] += 1
    sales_by_day_list = [(d.strftime('%Y-%m-%d'), sales_by_day[d]) for d in last7]

    kpis = {
        'total_count': total_count,
        'total_ingresos': round(total_ingresos, 2),
        'aov': round(aov, 2),
        'pct_unpaid': round(pct_unpaid, 2),
        'pct_unsent': round(pct_unsent, 2),
        'avg_time_to_payment': avg_time_to_payment,
        'top_products': top_products,
        'sales_by_day': sales_by_day_list
    }

    return render_template('index.html', ventas=ventas, total_count=total_count, kpis=kpis)

@app.route('/toggle/<int:venta_id>/<campo>')
def toggle(venta_id, campo):
    venta = Venta.query.get_or_404(venta_id)
    now = datetime.utcnow()
    if campo == 'pagado':
        venta.pagado = not venta.pagado
        venta.pagado_fecha = now if venta.pagado else None
    elif campo == 'enviado':
        venta.enviado = not venta.enviado
        venta.enviado_fecha = now if venta.enviado else None
    else:
        abort(400)
    db.session.commit()
    qs = request.query_string.decode('utf-8')
    return redirect(url_for('index') + ('?' + qs if qs else ''))

@app.route('/delete/<int:venta_id>', methods=['POST'])
def delete(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    db.session.delete(venta)
    db.session.commit()
    flash("Registro eliminado.", "info")
    qs = request.query_string.decode('utf-8')
    return redirect(url_for('index') + ('?' + qs if qs else ''))

@app.route('/upload/<int:venta_id>', methods=['POST'])
def upload_comprobante(venta_id):
    venta = Venta.query.get_or_404(venta_id)

    if 'file' not in request.files:
        flash("No se seleccionó archivo", "danger")
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        flash("Nombre de archivo vacío", "danger")
        return redirect(url_for('index'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 👉 carpeta por cliente
        client_folder = os.path.join(app.config['UPLOAD_FOLDER'], venta.cliente.replace(" ", "_"))
        os.makedirs(client_folder, exist_ok=True)

        # nombre final del archivo
        filename = f"{venta.cliente}_{timestamp}_{filename}"
        file_path = os.path.join(client_folder, filename)

        file.save(file_path)

        # guardamos en DB la ruta relativa
        venta.comprobante_path = file_path
        db.session.commit()

        flash("Comprobante guardado.", "success")
    else:
        flash("Formato no permitido", "danger")

    return redirect(url_for('index'))


@app.route('/export')
def export_xlsx():
    q = Venta.query
    q = apply_filters(q)
    ventas = q.order_by(Venta.fecha.desc()).all()
    df = pd.DataFrame([{
        'id': v.id,
        'cliente': v.cliente,
        'producto': v.producto,
        'cantidad': v.cantidad,
        'precio_unitario': v.precio_unitario,
        'total': v.total,
        'metodo_pago': v.metodo_pago,
        'fecha_utc': v.fecha,
        'pagado': v.pagado,
        'pagado_fecha_utc': v.pagado_fecha,
        'enviado': v.enviado,
        'enviado_fecha_utc': v.enviado_fecha
    } for v in ventas])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='ventas')
    buffer.seek(0)
    now = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    return send_file(buffer,
                    as_attachment=True,
                    download_name=f'ventas_{now}.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
