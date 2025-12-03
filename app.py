import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime, timezone
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-this-secret-key-in-production')

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'extreme_aps'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres')
}

# Login credentials (in production, use environment variables or database)
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'adminuser')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'adminpass')


def get_db_connection():
    """Create database connection"""
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


def login_required(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout"""
    session.clear()
    return redirect(url_for('login'))


import logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

@app.route('/')
@login_required
def dashboard():
    """Main dashboard with proper None handling"""
    error_msg = None
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get statistics - handle empty tables safely
        cur.execute("SELECT COUNT(*) as total FROM controllers")
        result = cur.fetchone()
        total_controllers = result['total'] if result else 0
        
        cur.execute("SELECT COUNT(*) as failed FROM controllers WHERE status = 0")
        result = cur.fetchone()
        failed_controllers = result['failed'] if result else 0
        
        cur.execute("SELECT COUNT(*) as total FROM access_points")
        result = cur.fetchone()
        total_aps = result['total'] if result else 0
        
        cur.execute("SELECT COUNT(*) as failed FROM access_points WHERE status != 'InService'")
        result = cur.fetchone()
        failed_aps = result['failed'] if result else 0
        
        # Failed Controllers Pagination
        failed_ctrl_page = request.args.get('failed_ctrl_page', 1, type=int)
        failed_ctrl_per_page = 10
        total_failed_ctrl_pages = max(1, (failed_controllers + failed_ctrl_per_page - 1) // failed_ctrl_per_page)

        # Validate page number
        if failed_ctrl_page < 1:
            failed_ctrl_page = 1
        elif failed_ctrl_page > total_failed_ctrl_pages:
            failed_ctrl_page = total_failed_ctrl_pages

        failed_ctrl_offset = (failed_ctrl_page - 1) * failed_ctrl_per_page

        # Get failed controllers with pagination
        cur.execute("""
            SELECT target_ip, host_name, fail_reason, first_failure_time
            FROM controllers 
            WHERE status = 0
            ORDER BY target_ip
            LIMIT %s OFFSET %s
        """, (failed_ctrl_per_page, failed_ctrl_offset))
        failed_controllers_list = cur.fetchall() or []
        
        # Controller Pagination & Search
        ctrl_page = request.args.get('ctrl_page', 1, type=int)
        ctrl_per_page = 20
        ctrl_offset = (ctrl_page - 1) * ctrl_per_page
        ctrl_search = request.args.get('ctrl_search', '').strip()
        ctrl_sort = request.args.get('ctrl_sort', 'target_ip')
        ctrl_order = request.args.get('ctrl_order', 'asc')
        
        valid_ctrl_sorts = {'target_ip': 'c.target_ip', 'host_name': 'c.host_name', 
                           'status': 'c.status', 'ap_count': 'ap_count'}
        sort_column = valid_ctrl_sorts.get(ctrl_sort, 'c.target_ip')
        order_clause = 'DESC' if ctrl_order == 'desc' else 'ASC'
        
        if ctrl_search:
            cur.execute(f"""
                SELECT c.target_ip, c.host_name, c.status, c.fail_reason, COUNT(ap.id) as ap_count
                FROM controllers c
                LEFT JOIN access_points ap ON c.target_ip = ap.target_ip
                WHERE c.target_ip ILIKE %s OR c.host_name ILIKE %s
                GROUP BY c.target_ip, c.host_name, c.status, c.fail_reason
                ORDER BY {sort_column} {order_clause}
                LIMIT %s OFFSET %s
            """, (f'%{ctrl_search}%', f'%{ctrl_search}%', ctrl_per_page, ctrl_offset))
        else:
            cur.execute(f"""
                SELECT c.target_ip, c.host_name, c.status, c.fail_reason, COUNT(ap.id) as ap_count
                FROM controllers c
                LEFT JOIN access_points ap ON c.target_ip = ap.target_ip
                GROUP BY c.target_ip, c.host_name, c.status, c.fail_reason
                ORDER BY {sort_column} {order_clause}
                LIMIT %s OFFSET %s
            """, (ctrl_per_page, ctrl_offset))
        
        all_controllers = cur.fetchall() or []
        
        # Get controller count
        if ctrl_search:
            cur.execute("""
                SELECT COUNT(DISTINCT c.target_ip) as total
                FROM controllers c
                WHERE c.target_ip ILIKE %s OR c.host_name ILIKE %s
            """, (f'%{ctrl_search}%', f'%{ctrl_search}%'))
        else:
            cur.execute("SELECT COUNT(*) as total FROM controllers")
        
        result = cur.fetchone()
        total_ctrl_records = result['total'] if result else 0
        total_ctrl_pages = max(1, (total_ctrl_records + ctrl_per_page - 1) // ctrl_per_page)
        
        # AP List with pagination, search, and sort - FIXED
        ap_page = request.args.get('ap_page', 1, type=int)
        ap_per_page = 50
        ap_offset = (ap_page - 1) * ap_per_page
        ap_search = request.args.get('ap_search', '').strip()
        ap_sort = request.args.get('ap_sort', 'status')
        ap_order = request.args.get('ap_order', 'asc')
        
        # Validate sort column
        valid_ap_sorts = {
            'status': 'status',
            'hostname': 'hostname', 
            'serial_number': 'serial_number',
            'ip_address': 'ip_address', 
            'target_ip': 'target_ip',
            'host_site': 'host_site',
            'mac_address': 'mac_address'
        }
        sort_column = valid_ap_sorts.get(ap_sort, 'status')
        order_clause = 'DESC' if ap_order == 'desc' else 'ASC'

        # Execute data query
        if ap_search:
            cur.execute(f"""
                SELECT *, 
                    target_ip as controller_ip,
                    'N/A' as controller_name
                FROM access_points
                WHERE hostname ILIKE %s OR ip_address ILIKE %s 
                OR mac_address ILIKE %s OR serial_number ILIKE %s
                ORDER BY {sort_column} {order_clause}
                LIMIT %s OFFSET %s
            """, (f'%{ap_search}%', f'%{ap_search}%', f'%{ap_search}%', 
                f'%{ap_search}%', ap_per_page, ap_offset))
        else:
            cur.execute(f"""
                SELECT *, 
                    target_ip as controller_ip,
                    'N/A' as controller_name
                FROM access_points
                ORDER BY {sort_column} {order_clause}
                LIMIT %s OFFSET %s
            """, (ap_per_page, ap_offset))
        
        all_aps = cur.fetchall() or []
        
        # Execute count query separately
        if ap_search:
            cur.execute("""
                SELECT COUNT(*) as total
                FROM access_points
                WHERE hostname ILIKE %s OR ip_address ILIKE %s 
                OR mac_address ILIKE %s OR serial_number ILIKE %s
            """, (f'%{ap_search}%', f'%{ap_search}%', f'%{ap_search}%', f'%{ap_search}%'))
        else:
            cur.execute("SELECT COUNT(*) as total FROM access_points")
        
        result = cur.fetchone()
        total_ap_records = result['total'] if result else 0
        total_ap_pages = max(1, (total_ap_records + ap_per_page - 1) // ap_per_page)
        
        cur.close()
        conn.close()
        
        # Debug output to console
        print(f"DEBUG: Total APs in DB: {total_aps}")
        print(f"DEBUG: APs fetched: {len(all_aps)}")
        if all_aps:
            print(f"DEBUG: Sample AP: {dict(all_aps[0])}")
        
        return render_template('dashboard.html',
                             total_controllers=total_controllers,
                             failed_controllers=failed_controllers,
                             total_aps=total_aps,
                             failed_aps=failed_aps,
                             failed_controllers_list=failed_controllers_list,
                             failed_ctrl_page=failed_ctrl_page,
                             total_failed_ctrl_pages=total_failed_ctrl_pages,
                             failed_ctrl_per_page=failed_ctrl_per_page,
                             all_controllers=all_controllers,
                             all_aps=all_aps,
                             ctrl_page=ctrl_page,
                             total_ctrl_pages=total_ctrl_pages,
                             ctrl_search=ctrl_search,
                             ctrl_sort=ctrl_sort,
                             ctrl_order=ctrl_order,
                             ap_page=ap_page,
                             total_ap_pages=total_ap_pages,
                             ap_search=ap_search,
                             ap_sort=ap_sort,
                             ap_order=ap_order,
                             error=error_msg)
                             
    except Exception as e:
        logging.error(f"Error in dashboard: {str(e)}", exc_info=True)
        error_msg = f"System error: {str(e)}"
    
    return render_template('dashboard.html',
                         total_controllers=0, failed_controllers=0, total_aps=0, failed_aps=0,
                         failed_controllers_list=[], all_controllers=[], all_aps=[],
                         ctrl_page=1, total_ctrl_pages=1, ctrl_search='', ctrl_sort='target_ip', ctrl_order='asc',
                         ap_page=1, total_ap_pages=1, ap_search='', ap_sort='status', ap_order='asc',
                         error=error_msg)


@app.route('/ap/<serial_number>')
@login_required
def ap_details(serial_number):
    """AP details page"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            ap.*,
            c.host_name as controller_name
        FROM access_points ap
        LEFT JOIN controllers c ON ap.target_ip = c.target_ip
        WHERE ap.serial_number = %s
    """, (serial_number,))
    
    ap = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if not ap:
        return "AP not found", 404
    
    return render_template('ap_details.html', ap=ap)


@app.route('/api/stats')
@login_required
def api_stats():
    """API endpoint for statistics (for auto-refresh)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) as total FROM controllers")
        result = cur.fetchone()
        total_controllers = result['total'] if result else 0
        
        cur.execute("SELECT COUNT(*) as failed FROM controllers WHERE status = 0")
        result = cur.fetchone()
        failed_controllers = result['failed'] if result else 0
        
        cur.execute("SELECT COUNT(*) as total FROM access_points")
        result = cur.fetchone()
        total_aps = result['total'] if result else 0
        
        cur.execute("SELECT COUNT(*) as failed FROM access_points WHERE status != 'InService'")
        result = cur.fetchone()
        failed_aps = result['failed'] if result else 0
        
        cur.close()
        conn.close()
        
        return jsonify({
            'total_controllers': total_controllers,
            'failed_controllers': failed_controllers,
            'total_aps': total_aps,
            'failed_aps': failed_aps
        })
    except Exception as e:
        return jsonify({
            'total_controllers': 0,
            'failed_controllers': 0,
            'total_aps': 0,
            'failed_aps': 0,
            'error': str(e)
        }), 500


@app.template_filter('format_uptime')
def format_uptime(seconds):
    """Format uptime in seconds to human-readable format"""
    if not seconds or seconds == 'N/A':
        return 'N/A'
    
    try:
        seconds = int(seconds)
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        
        return ' '.join(parts) if parts else '0m'
    except:
        return 'N/A'


@app.template_filter('format_timestamp')
def format_timestamp(timestamp):
    """Format unix timestamp to readable date"""
    if not timestamp:
        return 'N/A'
    try:
        dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except:
        return 'N/A'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
