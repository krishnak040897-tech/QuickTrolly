from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from config import Config
from models import db
import functools
import os
import razorpay

app = Flask(__name__)
app.config.from_object(Config)

# Initialize Razorpay with error handling
try:
    razorpay_client = razorpay.Client(auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET))
except Exception as e:
    print(f"Warning: Could not initialize Razorpay: {e}")
    razorpay_client = None

# --- Decorators ---
def login_required(f):
    @functools.wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap

def admin_required(f):
    @functools.wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('Access denied. Admins only.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return wrap

# --- Main Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        if db.create_user(name, email, password):
            flash('Account created! Please login.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Email already exists.', 'danger')
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = db.verify_user(email, password)
        if user:
            session['user_id'] = str(user['id'])
            session['name'] = user['name']
            session['email'] = user['email']
            session['role'] = user.get('role', 'user')
            if user.get('role') == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('scanner'))
        else:
            flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/scanner')
@login_required
def scanner():
    return render_template('scanner.html')

@app.route('/menu')
@login_required
def menu():
    return render_template('menu.html')

# --- API Routes ---
@app.route('/api/product/<qr_code>')
@login_required
def get_product_api(qr_code):
    product = db.get_product_by_qr(qr_code)
    if product:
        return jsonify({"success": True, "product": product})
    return jsonify({"success": False, "message": "Product not found"})

@app.route('/api/scan/<barcode>')
@login_required
def scan_barcode_api(barcode):
    product = db.get_product_by_barcode(barcode)
    if product:
        return jsonify({
            "success": True,
            "product": product,
            "message": f"Found: {product['name']}"
        })
    return jsonify({
        "success": False,
        "message": "Product not found. Please check the barcode."
    })

# --- Cart API Routes ---
@app.route('/api/cart', methods=['GET'])
@login_required
def get_cart():
    user_id = session.get('user_id')
    cart = db.get_user_cart(user_id)
    return jsonify({"success": True, "cart": cart})

@app.route('/api/cart/add', methods=['POST'])
@login_required
def add_to_cart():
    user_id = session.get('user_id')
    data = request.get_json() or {}
    product = data.get('product')
    if product and (product.get('_id') or product.get('id')):
        db.add_item_to_cart(user_id, product)
        return jsonify({"success": True, "message": "Item added"})
    return jsonify({"success": False, "message": "Invalid product data reference"})

@app.route('/api/cart/remove', methods=['POST'])
@login_required
def remove_from_cart():
    user_id = session.get('user_id')
    data = request.get_json() or {}
    product_id = data.get('product_id')
    if product_id:
        db.remove_item_from_cart(user_id, str(product_id))
        return jsonify({"success": True, "message": "Item removed"})
    return jsonify({"success": False, "message": "Invalid product reference mapping"})

@app.route('/api/cart/clear', methods=['POST'])
@login_required
def clear_cart():
    user_id = session.get('user_id')
    db.clear_cart(user_id)
    return jsonify({"success": True})

@app.route('/cart')
@login_required
def cart():
    return render_template('cart.html')

# --- Checkout & Payment Route ---
@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if request.method == 'POST':
        user_id = session.get('user_id')
        cart = db.get_user_cart(user_id)

        if not cart or len(cart) == 0:
            return jsonify({"success": False, "message": "Cart is empty"})

        data = request.get_json() or {}
        total = sum(item['price'] * item['qty'] for item in cart)
        razorpay_payment_id = data.get('razorpay_payment_id')
        
        if not razorpay_payment_id:
             return jsonify({"success": False, "message": "Payment failed or cancelled"})

        user_email = session.get('email')

        order_id = db.create_order(
            customer_name=data.get('name'),
            phone=data.get('phone'),
            address=data.get('address'),
            products=cart,
            total=total,
            email=user_email,
            payment_id=razorpay_payment_id
        )
        
        db.clear_cart(user_id)
        return jsonify({"success": True, "order_id": order_id})

    return render_template('checkout.html', key_id=Config.RAZORPAY_KEY_ID)

@app.route('/success')
@login_required
def success():
    order_id = request.args.get('order_id')
    order = None
    if order_id:
        order = db.get_order_by_id(order_id)
    return render_template('success.html', order=order)

# --- Product Routes ---
@app.route('/products')
@login_required
def view_products():
    products = db.get_all_products()
    return render_template('view_products.html', products=products)

@app.route('/product/<product_id>')
@login_required
def product_details(product_id):
    product = db.get_product_by_id(product_id)
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('view_products'))
    return render_template('product_details.html', product=product)

# --- Admin Routes ---
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    total_prod, total_ord = db.get_stats()
    total_users = db.get_user_count()
    recent_products = db.get_recent_products(5)
    
    # Process updated financial data metrics
    total_revenue, daily_revenue, monthly_revenue = db.get_revenue_stats()
    
    return render_template('admin/dashboard.html',
                           total_prod=total_prod,
                           total_ord=total_ord,
                           total_users=total_users,
                           recent_products=recent_products,
                           total_revenue=total_revenue,
                           daily_revenue=daily_revenue,
                           monthly_revenue=monthly_revenue)

@app.route('/admin/products')
@admin_required
def admin_products():
    products = db.get_all_products()
    return render_template('products.html', products=products)

@app.route('/admin/transactions')
@admin_required
def admin_transactions():
    orders = db.get_all_orders()
    return render_template('admin/transactions.html', orders=orders)

@app.route('/admin/add_product', methods=['GET', 'POST'])
@admin_required
def admin_add_product():
    if request.method == 'POST':
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        original_price = data.get('original_price', '').strip()
        price = data.get('price', '').strip()
        qr_code = data.get('qr_code', '').strip()
        image = data.get('image', '').strip()

        if not name or not original_price or not price or not qr_code:
            return jsonify({"success": False, "message": "Name, Original Price, Selling Price and QR Code are required."})
        try:
            float(original_price)
            float(price)
        except ValueError:
            return jsonify({"success": False, "message": "Prices must be valid numerical parameters."})
        if db.get_product_by_qr(qr_code):
            return jsonify({"success": False, "message": "A product with this QR Code already exists."})

        product_id = db.add_product(name, original_price, price, qr_code, image or 'https://placehold.co/400x300/f5f5f5/999999?text=No+Image')
        
        # FIX: Fetch the product immediately to get the generated barcode path
        product = db.get_product_by_id(product_id)
        
        return jsonify({
            "success": True, 
            "message": "Product added successfully.", 
            "product_id": product_id,
            "product": product
        })
    return render_template('admin/add_product.html')

@app.route('/admin/edit_product/<product_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_product(product_id):
    product = db.get_product_by_id(product_id)
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('admin_products'))
    if request.method == 'POST':
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        original_price = data.get('original_price', '').strip()
        price = data.get('price', '').strip()
        qr_code = data.get('qr_code', '').strip()
        image = data.get('image', '').strip()

        if not name or not original_price or not price or not qr_code:
            return jsonify({"success": False, "message": "Name, Original Price, Selling Price and QR Code are required."})
        try:
            float(original_price)
            float(price)
        except ValueError:
            return jsonify({"success": False, "message": "Prices must be valid numbers."})

        existing = db.get_product_by_qr(qr_code)
        if existing and str(existing['id']) != product_id:
            return jsonify({"success": False, "message": "A product with this QR Code already exists."})

        db.update_product(product_id, name, original_price, price, qr_code, image or 'https://placehold.co/400x300/f5f5f5/999999?text=No+Image')
        return jsonify({"success": True, "message": "Product updated successfully."})

    return render_template('edit_product.html', product=product)

@app.route('/admin/regenerate_barcode/<product_id>', methods=['POST'])
@admin_required
def regenerate_barcode(product_id):
    result = db.regenerate_barcode(product_id)
    if result:
        return jsonify({"success": True, "barcode": result})
    return jsonify({"success": False, "message": "Product not found."})

@app.route('/admin/download_barcode/<product_id>')
@admin_required
def download_barcode(product_id):
    product = db.get_product_by_id(product_id)
    if product and product.get('barcode_image'):
        barcode_path = product['barcode_image']
        
        # FIX: If it is a remote URL (API Fallback), redirect to it
        if barcode_path.startswith('http'):
            return redirect(barcode_path)

        # If it is a local path, send the file
        filepath = barcode_path.lstrip('/')
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True, download_name=f"barcode_{product.get('barcode_number', 'unknown')}.png")
            
    flash('Barcode not found.', 'danger')
    return redirect(url_for('admin_products'))

@app.route('/admin/delete_product/<product_id>', methods=['POST'])
@admin_required
def admin_delete_product(product_id):
    deleted = db.delete_product(product_id)
    if deleted:
        return jsonify({"success": True, "message": "Product deleted successfully."})
    return jsonify({"success": False, "message": "Product not found."})

@app.route('/admin/update_order_status/<order_id>', methods=['POST'])
@admin_required
def update_order_status(order_id):
    data = request.get_json() or {}
    new_status = data.get('status')
    if new_status:
        db.update_order_status(order_id, new_status)
        return jsonify({"success": True, "message": "Status updated"})
    return jsonify({"success": False, "message": "Invalid status"})

@app.route('/admin/api/products')
@admin_required
def admin_api_products():
    search = request.args.get('search', '').strip()
    if search:
        products = db.search_products(search)
    else:
        products = db.get_all_products()
    return jsonify({"success": True, "products": products})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)