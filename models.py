import psycopg2
import psycopg2.extras
import psycopg2.errors
import json
import random
import io
import os
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from datetime import datetime
import cloudinary
import cloudinary.uploader

# Configure Cloudinary
cloudinary.config(
    cloud_name=Config.CLOUDINARY_CLOUD_NAME,
    api_key=Config.CLOUDINARY_API_KEY,
    api_secret=Config.CLOUDINARY_API_SECRET
)

try:
    import barcode
    from barcode.writer import ImageWriter
    from barcode.codex import Code128
    BARCODE_AVAILABLE = True
except ImportError:
    BARCODE_AVAILABLE = False
    print("WARNING: 'python-barcode' library not found. Will use online API fallback.")


class Database:
    def __init__(self):
        self.database_url = Config.DATABASE_URL
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is not set.")
        
        # Connect to PostgreSQL using DictCursor to mimic sqlite3.Row behavior
        self.conn = psycopg2.connect(self.database_url, cursor_factory=psycopg2.extras.DictCursor)
        self.cursor = self.conn.cursor()
        
        self._create_tables()
        self._migrate_db()
        self._init_admin()

    def _create_tables(self):
        # Users Table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS quicktrolly_users (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(50) DEFAULT 'user',
                cart TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Products Table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS quicktrolly_products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                original_price REAL NOT NULL,
                price REAL NOT NULL,
                qr_code VARCHAR(255) UNIQUE NOT NULL,
                image TEXT DEFAULT '',
                barcode_number VARCHAR(255) UNIQUE,
                barcode_image TEXT DEFAULT NULL,
                barcode_public_id TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NULL
            )
        ''')

        # Orders Table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS quicktrolly_orders (
                id SERIAL PRIMARY KEY,
                customer_name VARCHAR(255) NOT NULL,
                phone VARCHAR(50),
                address TEXT,
                email VARCHAR(255),
                payment_id VARCHAR(255),
                products TEXT NOT NULL,
                total REAL NOT NULL,
                date TIMESTAMP NOT NULL,
                status VARCHAR(50) DEFAULT 'Pending'
            )
        ''')
        self.conn.commit()

    def _migrate_db(self):
        # Add original_price column if it doesn't exist
        try:
            self.cursor.execute("ALTER TABLE quicktrolly_products ADD COLUMN original_price REAL DEFAULT 0.0")
            self.conn.commit()
        except psycopg2.errors.UndefinedColumn:
            self.conn.rollback()
            pass
        except Exception as e:
            print(f"Migration error: {e}")
            self.conn.rollback()
            pass

    def _generate_unique_barcode(self):
        while True:
            barcode_num = ''.join([str(random.randint(0, 9)) for _ in range(12)])
            self.cursor.execute('SELECT id FROM quicktrolly_products WHERE barcode_number = %s', (barcode_num,))
            if not self.cursor.fetchone():
                return barcode_num

    def _generate_barcode_image(self, barcode_number, product_id):
        if BARCODE_AVAILABLE:
            try:
                code128 = Code128(barcode_number, writer=ImageWriter())
                buffer = io.BytesIO()
                
                options = {
                    'module_width': 0.3,
                    'module_height': 15.0,
                    'quiet_zone': 6.5,
                    'font_size': 10,
                    'text_distance': 5.0
                }
                # Write to memory buffer
                code128.write(buffer, options)
                buffer.seek(0)
                
                # Upload to Cloudinary
                response = cloudinary.uploader.upload(
                    buffer, 
                    folder='quicktrolly/barcodes/', 
                    public_id=f'barcode_{product_id}',
                    resource_type='image',
                    format='png'
                )
                
                return response['secure_url'], response['public_id']
            except Exception as e:
                print(f"Error uploading barcode to Cloudinary: {e}. Falling back to API.")
        
        # FALLBACK: Use external API
        print(f"Using API for barcode: {barcode_number}")
        return f"https://barcodeapi.org/api/code128/{barcode_number}", None

    def _init_admin(self):
        self.cursor.execute('SELECT id FROM quicktrolly_users WHERE email = %s', ('admin',))
        if not self.cursor.fetchone():
            hashed_pw = generate_password_hash("admin123")
            self.cursor.execute('''
                INSERT INTO quicktrolly_users (name, email, password, role, cart)
                VALUES (%s, %s, %s, %s, %s)
            ''', ("Admin User", "admin", hashed_pw, "admin", "[]"))
            self.conn.commit()
            print("✅ Default Admin Created: admin / admin123")

    def _row_to_dict(self, row):
        if row is None:
            return None
        return dict(row)

    def create_user(self, name, email, password):
        self.cursor.execute('SELECT id FROM quicktrolly_users WHERE email = %s', (email,))
        if self.cursor.fetchone():
            return False
        
        hashed_pw = generate_password_hash(password)
        try:
            self.cursor.execute('''
                INSERT INTO quicktrolly_users (name, email, password, role, cart)
                VALUES (%s, %s, %s, %s, %s)
            ''', (name, email, hashed_pw, "user", "[]"))
            self.conn.commit()
            return True
        except psycopg2.errors.UniqueViolation:
            return False

    def verify_user(self, email, password):
        self.cursor.execute('SELECT * FROM quicktrolly_users WHERE email = %s', (email,))
        user = self.cursor.fetchone()
        if user and check_password_hash(user['password'], password):
            return self._row_to_dict(user)
        return None

    def get_user_cart(self, user_id):
        try:
            self.cursor.execute('SELECT cart FROM quicktrolly_users WHERE id = %s', (user_id,))
            row = self.cursor.fetchone()
            if row:
                try:
                    return json.loads(row['cart'])
                except (json.JSONDecodeError, TypeError):
                    return []
            return []
        except Exception as e:
            print(f"Error getting cart: {e}")
            return []

    def update_user_cart(self, user_id, cart_data):
        cart_json = json.dumps(cart_data)
        self.cursor.execute('UPDATE quicktrolly_users SET cart = %s WHERE id = %s', (cart_json, user_id))
        self.conn.commit()

    def add_item_to_cart(self, user_id, product):
        cart = self.get_user_cart(user_id)
        p_id = str(product.get('_id') or product.get('id'))
        existing = next((item for item in cart if (str(item.get('_id')) == p_id or str(item.get('id')) == p_id)), None)
        
        if existing:
            existing['qty'] = existing.get('qty', 0) + 1
        else:
            product['qty'] = 1
            if '_id' not in product:
                product['_id'] = p_id
            cart.append(product)
        
        self.update_user_cart(user_id, cart)
        return True

    def remove_item_from_cart(self, user_id, product_id):
        cart = self.get_user_cart(user_id)
        p_str = str(product_id)
        item_index = next((i for i, item in enumerate(cart) if (str(item.get('_id')) == p_str or str(item.get('id')) == p_str)), None)
        
        if item_index is not None:
            if cart[item_index].get('qty', 1) > 1:
                cart[item_index]['qty'] -= 1
            else:
                cart.pop(item_index)
        
        self.update_user_cart(user_id, cart)
        return True

    def clear_cart(self, user_id):
        self.cursor.execute('UPDATE quicktrolly_users SET cart = %s WHERE id = %s', ("[]", user_id))
        self.conn.commit()

    def get_all_products(self):
        try:
            self.cursor.execute('SELECT * FROM quicktrolly_products ORDER BY created_at DESC')
            rows = self.cursor.fetchall()
            products = []
            for row in rows:
                product = self._row_to_dict(row)
                product['_id'] = str(product['id'])
                products.append(product)
            return products
        except Exception as e:
            print(f"Error fetching products: {e}")
            return []

    def get_product_by_qr(self, qr_code):
        self.cursor.execute('SELECT * FROM quicktrolly_products WHERE qr_code = %s', (qr_code,))
        row = self.cursor.fetchone()
        if row:
            product = self._row_to_dict(row)
            product['_id'] = str(product['id'])
            return product
        return None
    
    def get_product_by_barcode(self, barcode_number):
        self.cursor.execute('SELECT * FROM quicktrolly_products WHERE barcode_number = %s', (barcode_number,))
        row = self.cursor.fetchone()
        if row:
            product = self._row_to_dict(row)
            product['_id'] = str(product['id'])
            return product
        return None

    def get_product_by_id(self, product_id):
        self.cursor.execute('SELECT * FROM quicktrolly_products WHERE id = %s', (product_id,))
        row = self.cursor.fetchone()
        if row:
            product = self._row_to_dict(row)
            product['_id'] = str(product['id'])
            return product
        return None

    def add_product(self, name, original_price, price, qr_code, image_url):
        barcode_number = self._generate_unique_barcode()
        self.cursor.execute('''
            INSERT INTO quicktrolly_products (name, original_price, price, qr_code, image, barcode_number, barcode_image, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (name, float(original_price), float(price), qr_code, image_url, barcode_number, None, datetime.now()))
        self.conn.commit()
        
        product_id = self.cursor.lastrowid
        barcode_image, barcode_public_id = self._generate_barcode_image(barcode_number, product_id)
        
        self.cursor.execute('UPDATE quicktrolly_products SET barcode_image = %s, barcode_public_id = %s WHERE id = %s', (barcode_image, barcode_public_id, product_id))
        self.conn.commit()
        return str(product_id)

    def update_product(self, product_id, name, original_price, price, qr_code, image_url):
        existing = self.get_product_by_id(product_id)
        if not existing:
            return
        
        barcode_number = existing.get('barcode_number')
        barcode_image = existing.get('barcode_image')
        barcode_public_id = existing.get('barcode_public_id')
        
        if not barcode_number:
            barcode_number = self._generate_unique_barcode()
            barcode_image, barcode_public_id = self._generate_barcode_image(barcode_number, product_id)
        
        self.cursor.execute('''
            UPDATE quicktrolly_products 
            SET name = %s, original_price = %s, price = %s, qr_code = %s, image = %s, barcode_number = %s, 
                barcode_image = %s, updated_at = %s
            WHERE id = %s
        ''', (name, float(original_price), float(price), qr_code, image_url, barcode_number, barcode_image, 
              datetime.now(), product_id))
        self.conn.commit()

    def delete_product(self, product_id):
        product = self.get_product_by_id(product_id)
        if product and product.get('barcode_public_id'):
            try:
                cloudinary.uploader.destroy(product['barcode_public_id'])
            except Exception as e:
                print(f"Error deleting barcode from Cloudinary: {e}")
        
        self.cursor.execute('DELETE FROM quicktrolly_products WHERE id = %s', (product_id,))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def regenerate_barcode(self, product_id):
        product = self.get_product_by_id(product_id)
        if not product:
            return None
        
        if product.get('barcode_public_id'):
            try:
                cloudinary.uploader.destroy(product['barcode_public_id'])
            except Exception as e:
                print(f"Error deleting old barcode: {e}")
        
        barcode_number = self._generate_unique_barcode()
        barcode_image, barcode_public_id = self._generate_barcode_image(barcode_number, product_id)
        
        self.cursor.execute('''
            UPDATE quicktrolly_products 
            SET barcode_number = %s, barcode_image = %s, barcode_public_id = %s
            WHERE id = %s
        ''', (barcode_number, barcode_image, barcode_public_id, product_id))
        self.conn.commit()
        return {"barcode_number": barcode_number, "barcode_image": barcode_image}

    def create_order(self, customer_name, phone, address, products, total, email=None, payment_id=None):
        products_json = json.dumps(products)
        order_date = datetime.now()
        
        self.cursor.execute('''
            INSERT INTO quicktrolly_orders (customer_name, phone, address, email, payment_id, products, total, date, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (customer_name, phone, address, email, payment_id, products_json, total, order_date, "Pending"))
        self.conn.commit()
        return str(self.cursor.lastrowid)

    def get_order_by_id(self, order_id):
        self.cursor.execute('SELECT * FROM quicktrolly_orders WHERE id = %s', (order_id,))
        row = self.cursor.fetchone()
        if row:
            order = self._row_to_dict(row)
            order['_id'] = str(order['id'])
            try:
                order['products'] = json.loads(order['products'])
            except (json.JSONDecodeError, TypeError):
                order['products'] = []
            return order
        return None

    def get_all_orders(self):
        self.cursor.execute('SELECT * FROM quicktrolly_orders ORDER BY date DESC')
        rows = self.cursor.fetchall()
        orders = []
        for row in rows:
            order = self._row_to_dict(row)
            order['_id'] = str(order['id'])
            try:
                order['products'] = json.loads(order['products'])
            except (json.JSONDecodeError, TypeError):
                order['products'] = []
            orders.append(order)
        return orders

    def update_order_status(self, order_id, status):
        self.cursor.execute('UPDATE quicktrolly_orders SET status = %s WHERE id = %s', (status, order_id))
        self.conn.commit()

    def get_stats(self):
        try:
            self.cursor.execute('SELECT COUNT(*) FROM quicktrolly_products')
            total_products = self.cursor.fetchone()[0]
            self.cursor.execute('SELECT COUNT(*) FROM quicktrolly_orders')
            total_orders = self.cursor.fetchone()[0]
            return total_products, total_orders
        except Exception as e:
            print(f"Stats error: {e}")
            return 0, 0

    def get_user_count(self):
        try:
            self.cursor.execute('SELECT COUNT(*) FROM quicktrolly_users')
            return self.cursor.fetchone()[0]
        except Exception as e:
            print(f"User count error: {e}")
            return 0

    def get_recent_products(self, limit=5):
        try:
            self.cursor.execute('SELECT * FROM quicktrolly_products ORDER BY created_at DESC LIMIT %s', (limit,))
            rows = self.cursor.fetchall()
            products = []
            for row in rows:
                product = self._row_to_dict(row)
                product['_id'] = str(product['id'])
                products.append(product)
            return products
        except Exception as e:
            print(f"Recent products error: {e}")
            return []

    def search_products(self, search_term):
        try:
            search_pattern = f'%{search_term}%'
            self.cursor.execute('''
                SELECT * FROM quicktrolly_products 
                WHERE name LIKE %s OR qr_code LIKE %s OR barcode_number LIKE %s
                ORDER BY created_at DESC
            ''', (search_pattern, search_pattern, search_pattern))
            rows = self.cursor.fetchall()
            products = []
            for row in rows:
                product = self._row_to_dict(row)
                product['_id'] = str(product['id'])
                products.append(product)
            return products
        except Exception as e:
            print(f"Search error: {e}")
            return []

    def get_revenue_stats(self):
        try:
            # 1. Total Lifetime Revenue
            self.cursor.execute("SELECT SUM(total) FROM quicktrolly_orders WHERE status IN %s", (('Settled', 'Pending', 'Auditing'),))
            result = self.cursor.fetchone()[0]
            total_revenue = float(result) if result is not None else 0.0

            # 2. Daily Revenue Splits
            self.cursor.execute('''
                SELECT to_char(date, 'YYYY-MM-DD') as revenue_day, SUM(total) as gross_amount, COUNT(id) as operational_count
                FROM quicktrolly_orders 
                WHERE status IN %s
                GROUP BY revenue_day
                ORDER BY revenue_day DESC
                LIMIT 10
            ''', (('Settled', 'Pending', 'Auditing'),))
            daily_breakdown = [dict(row) for row in self.cursor.fetchall()]

            # 3. Monthly Revenue Splits
            self.cursor.execute('''
                SELECT to_char(date, 'YYYY-MM') as revenue_month, SUM(total) as gross_amount, COUNT(id) as operational_count
                FROM quicktrolly_orders 
                WHERE status IN %s
                GROUP BY revenue_month
                ORDER BY revenue_month DESC
            ''', (('Settled', 'Pending', 'Auditing'),))
            monthly_breakdown = [dict(row) for row in self.cursor.fetchall()]

            return total_revenue, daily_breakdown, monthly_breakdown
        except Exception as e:
            print(f"Revenue stats error: {e}")
            return 0.0, [], []

db = Database()
