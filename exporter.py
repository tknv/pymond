import os
import csv
import time
import logging
import requests
import schedule
import threading
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

# --- Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s'
)

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'extreme_aps'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres')
}

# Exporter settings
POLLING_INTERVAL = 600  # 10 minutes
TOKEN_LIFETIME_MINUTES = 110
REQUEST_TIMEOUT = 10
MAX_WORKERS = 30
CSV_FILE_PATH = 'targets.csv'

# Token management
token_cache = {}
token_lock = threading.Lock()

# Failure history
failure_history = {}
failure_lock = threading.Lock()


def get_db_connection():
    """Create database connection"""
    return psycopg2.connect(**DB_CONFIG)


def init_database():
    """Initialize database tables"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Controllers table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS controllers (
            target_ip VARCHAR(45) PRIMARY KEY,
            host_name VARCHAR(255),
            status INTEGER DEFAULT 0,
            fail_reason TEXT,
            first_failure_time BIGINT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Access Points table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_points (
            id SERIAL PRIMARY KEY,
            target_ip VARCHAR(45),
            hostname VARCHAR(255),
            serial_number VARCHAR(100) UNIQUE,
            ip_address VARCHAR(45),
            mac_address VARCHAR(17),
            hardware_type VARCHAR(100),
            software_version VARCHAR(100),
            status VARCHAR(50),
            status_value INTEGER,
            floor_name VARCHAR(255),
            host_site VARCHAR(255),
            sys_uptime BIGINT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (target_ip) REFERENCES controllers(target_ip) ON DELETE CASCADE
        )
    """)
    
    # Create indexes for search
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ap_hostname ON access_points(hostname)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ap_ip ON access_points(ip_address)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ap_mac ON access_points(mac_address)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ap_serial ON access_points(serial_number)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_ap_status ON access_points(status_value)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_controller_ip ON controllers(target_ip)
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    logging.info("Database initialized successfully")


def parse_error_message(response):
    """Extract human-readable error message from HTTP response"""
    status_code = response.status_code
    status_descriptions = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        422: "Authentication Failed",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout"
    }
    
    try:
        error_data = response.json()
        api_error = error_data.get('errorMessage') or error_data.get('message') or error_data.get('error')
        
        if api_error:
            if status_code == 422 and ('password' in api_error.lower() or 'credential' in api_error.lower()):
                return "Authentication Failed: Invalid username or password"
            return f"HTTP {status_code}: {api_error}"
        return f"HTTP {status_code}: {status_descriptions.get(status_code, 'Unknown Error')}"
    except (ValueError, KeyError):
        response_text = response.text[:200].strip()
        if response_text:
            return f"HTTP {status_code}: {status_descriptions.get(status_code, '')} - {response_text}"
        return f"HTTP {status_code}: {status_descriptions.get(status_code, 'Unknown Error')}"


def get_token(ip, username, password):
    """Get API token with caching"""
    with token_lock:
        now = datetime.now(timezone.utc)
        cache_entry = token_cache.get(ip)

        if cache_entry and cache_entry['expires_at'] > now:
            logging.debug(f"[{ip}] Using cached token")
            return cache_entry['token'], None

        logging.info(f"[{ip}] Requesting new token...")
        token_url = f"https://{ip}:5825/management/v1/oauth2/token"
        payload = {
            'userId': username,
            'password': password,
            'grantType': 'password',
            'scope': '...'
        }
        
        try:
            response = requests.post(token_url, json=payload, verify=False, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            access_token = data.get('access_token')
            if not access_token:
                error_msg = data.get('errorMessage') or data.get('message') or 'access_token not found in response'
                logging.error(f"[{ip}] {error_msg}")
                return None, f"Token Error: {error_msg}"

            expires_at = now + timedelta(minutes=TOKEN_LIFETIME_MINUTES)
            token_cache[ip] = {'token': access_token, 'expires_at': expires_at}
            logging.info(f"[{ip}] Token obtained successfully")
            return access_token, None

        except requests.exceptions.HTTPError as e:
            return None, parse_error_message(e.response)
        except requests.exceptions.Timeout:
            return None, "Connection Timeout"
        except requests.exceptions.ConnectionError:
            return None, "Connection Refused or Network Error"
        except requests.exceptions.RequestException as e:
            return None, f"Request Error: {type(e).__name__}"


def update_controller_status(conn, ip, host_name, status, fail_reason=None):
    """Update controller status in database"""
    cur = conn.cursor()
    
    first_failure_time = None
    if status == 0:
        with failure_lock:
            if ip not in failure_history or not failure_history[ip].get('is_failing'):
                first_failure_time = int(datetime.now(timezone.utc).timestamp())
                failure_history[ip] = {'first_failure_time': first_failure_time, 'is_failing': True}
            else:
                first_failure_time = failure_history[ip]['first_failure_time']
    else:
        with failure_lock:
            if ip in failure_history and failure_history[ip].get('is_failing'):
                failure_history[ip]['is_failing'] = False
                first_failure_time = None
    
    cur.execute("""
        INSERT INTO controllers (target_ip, host_name, status, fail_reason, first_failure_time, last_update)
        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (target_ip) 
        DO UPDATE SET 
            host_name = EXCLUDED.host_name,
            status = EXCLUDED.status,
            fail_reason = EXCLUDED.fail_reason,
            first_failure_time = EXCLUDED.first_failure_time,
            last_update = CURRENT_TIMESTAMP
    """, (ip, host_name, status, fail_reason, first_failure_time))
    
    conn.commit()
    cur.close()


def collect_metrics_for_target(target):
    """Collect AP information from a single target"""
    ip = target['ip_address']
    username = target['username']
    password = target['password']
    host_name = target.get('host_name', 'N/A')
    
    logging.info(f"[{ip}] Starting metric collection")
    
    conn = get_db_connection()
    
    # Mark controller as failed initially
    update_controller_status(conn, ip, host_name, 0, '')
    
    token, token_error = get_token(ip, username, password)
    if not token:
        fail_reason = token_error or "Unknown token error"
        logging.warning(f"[{ip}] Token failure: {fail_reason}")
        update_controller_status(conn, ip, host_name, 0, fail_reason)
        conn.close()
        return

    ap_query_url = f"https://{ip}:5825/management/v1/aps/query"
    headers = {'Authorization': f"Bearer {token}", 'Accept': 'application/json'}

    try:
        response = requests.get(ap_query_url, headers=headers, verify=False, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        response_json = response.json()
        data = response_json.get('data', []) if isinstance(response_json, dict) else response_json
        
        logging.info(f"[{ip}] Successfully scraped {len(data)} APs")
        
        # Prepare AP data for batch insert
        ap_records = []
        for ap in data:
            serial_number = ap.get('serialNumber')
            if not serial_number:
                continue
            
            status = ap.get('status', 'N/A')
            status_value = 1 if status == 'InService' else 0
            
            ap_records.append((
                ip,
                ap.get('hostname', 'N/A'),
                serial_number,
                ap.get('ipAddress', 'N/A'),
                ap.get('macAddress', 'N/A'),
                ap.get('hardwareType', 'N/A'),
                ap.get('softwareVersion', 'N/A'),
                status,
                status_value,
                ap.get('floorName', 'N/A'),
                ap.get('hostSite', 'N/A'),
                ap.get('sysUptime', 0)
            ))
        
        if ap_records:
            cur = conn.cursor()
            execute_values(cur, """
                INSERT INTO access_points 
                (target_ip, hostname, serial_number, ip_address, mac_address, 
                 hardware_type, software_version, status, status_value, 
                 floor_name, host_site, sys_uptime)
                VALUES %s
                ON CONFLICT (serial_number) 
                DO UPDATE SET
                    target_ip = EXCLUDED.target_ip,
                    hostname = EXCLUDED.hostname,
                    ip_address = EXCLUDED.ip_address,
                    mac_address = EXCLUDED.mac_address,
                    hardware_type = EXCLUDED.hardware_type,
                    software_version = EXCLUDED.software_version,
                    status = EXCLUDED.status,
                    status_value = EXCLUDED.status_value,
                    floor_name = EXCLUDED.floor_name,
                    host_site = EXCLUDED.host_site,
                    sys_uptime = EXCLUDED.sys_uptime,
                    last_update = CURRENT_TIMESTAMP
            """, ap_records)
            conn.commit()
            cur.close()
        
        update_controller_status(conn, ip, host_name, 1, None)
        logging.info(f"[{ip}] Metric collection completed successfully")

    except requests.exceptions.HTTPError as e:
        fail_reason = parse_error_message(e.response)
        logging.error(f"[{ip}] Failed to scrape: {fail_reason}")
        update_controller_status(conn, ip, host_name, 0, fail_reason)
    except requests.exceptions.Timeout:
        update_controller_status(conn, ip, host_name, 0, "Connection Timeout")
    except requests.exceptions.ConnectionError:
        update_controller_status(conn, ip, host_name, 0, "Connection Refused or Network Error")
    except Exception as e:
        fail_reason = f"Unexpected Error: {type(e).__name__}"
        logging.error(f"[{ip}] {fail_reason}: {str(e)}")
        update_controller_status(conn, ip, host_name, 0, fail_reason)
    finally:
        conn.close()


def load_targets_from_csv():
    """Load targets from CSV file"""
    logging.info(f"Loading targets from CSV: {CSV_FILE_PATH}")
    targets = []
    
    if not os.path.exists(CSV_FILE_PATH):
        logging.error(f"CSV file not found: {CSV_FILE_PATH}")
        return []
    
    try:
        with open(CSV_FILE_PATH, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'ip_address' in row and 'username' in row and 'password' in row:
                    targets.append(row)
        logging.info(f"Loaded {len(targets)} targets")
        return targets
    except Exception as e:
        logging.error(f"Failed to read CSV: {e}")
        return []


def collect_all_metrics():
    """Scheduled job to poll all targets"""
    logging.info("=" * 60)
    logging.info("Starting scheduled metric collection...")
    logging.info("=" * 60)
    
    targets = load_targets_from_csv()
    if not targets:
        logging.warning("No targets loaded")
        return

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.map(collect_metrics_for_target, targets)
    
    logging.info("Scheduled metric collection finished")


def run_scheduler():
    """Run scheduler in separate thread"""
    logging.info("Scheduler thread started")
    collect_all_metrics()
    schedule.every(POLLING_INTERVAL).seconds.do(collect_all_metrics)
    
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    logging.info("=" * 60)
    logging.info("Extreme AP Exporter Starting")
    logging.info(f"Polling interval: {POLLING_INTERVAL} seconds")
    logging.info(f"CSV file: {CSV_FILE_PATH}")
    logging.info("=" * 60)
    
    # Initialize database
    init_database()
    
    # Start scheduler
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
