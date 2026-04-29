import json
import time
import urllib.request
import urllib.parse
import os
import jwt

PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDXXm3hHfXkm5N8
fFLSm1J1RdUf5zEgADeT37dHmC9lfsNQc98R+IH8lWfpWDKdIijkFguYp8RyOXg9
o+Rw+z8mxD1GtybGK1Y6TKM0dV1y1UI0wUXs9EImZh0JZ9fkkT4ZnwUdoMJr3RP7
PVxgdR+QiyEM/v+kLtETcKBKwkQ8SH1pGQ+zkUi5tswiD6pKAvvZIHbElXicOt8V
h+tYNZ60jrlC11rxR6MvyaGmm29IJXvYB/nrGWE7cOsOItM/XEhauyasYz43dLEO
eMd6wDSwse7Yg13xuSdxCR6g7ZCU/1tg5UxwlJQZg3HWFD4PzXbWo4ZpPziH5DZl
3BbHXhPBAgMBAAECggEATSERy9nNepIm5yGlDay3gq1ALt0+sB2zyb8IZdkAPGKB
8zmUDV0IvFsLbDt8YN7fcxPcEk8e4ONpBvY/dSDdlHYaFc240qapilZw6nQPw/TQ
8ZC4CKzPVe4i/v/UBxTm/wj+rYYpqrRxl/v5Lcg0RjE1ZHCcQAHOOZ6PoUZiZo3M
4VRbh+1vDSAzhP12I8VvShz77xQWYsRckKMeVhakCjBTSLqN+3krIjmmizH//XYZ
3ThUDQAHk8r0T3xdEkVwu6gHbG+wlW/gyRIo+xlUvW0ygQE7rxKinfnSCcBx/cuK
6ogJ2+B5wbg1S778NevYdw5kGnH6FTpWMrRa3OKhewKBgQD6/ico+a5COoS/GQLN
Vu6QitdT5gEUKhF082C47/9ZP0sF95ZCIhNqqM681d59a1KQG0aW67lvW1ssNMQZ
1WUNKd4yj41ao7R18jicw4za5oUKvaL+b35vgCRcFtwiyo4GnbbnJzm0yLpLhNAX
14oLeHbO9Cqy9LUJY5Inks8rHwKBgQDbqldQQbXhWI25qzAG3oYtIM9kza8a0Vd6
XQ5YSpaG5JM0O+/NbFgM1tW2m7YyU0ElhU+8LBENKXJ5DctKIAv6FCFhM9UTfz14
vh8E2+Z46pkXoQBE+pJZ6c9aqxF93ZRGnACvLzpy6qZrrtennqIflFpUSjVGr8Xx
Yjk1EcfFHwKBgG/YWXL08CYrFYRijNEb7+sRM6r7T3fA106aNXuz1sHaZoAeOCL4
NFbPKnETjLWu4Xe979LI8DUjLJmiWdB5OzKGebFmLsM5N+1nJrsUmOvi1V5X6w4e
eOUO4ST4Oth2Epv4I80Vua1J1VpZsLEvJyC9aZQHUg+05AdvC23/CbpZAoGBANZT
da3Q721XwbCO3uVU6QXPJYvtRBSefQPfF8f9vrEPBLHKaUe9louwcgUiGLsxkDbT
w+CN/nUhI5gJZXiFnCn8yjTVJelIFOpiVlGfXVhNTeJILMMg2PrxrmeA0ihEsg/S
1rgXFKhbWtVmWvQpS3YUga4MWb8GcdP7SmYFWy1xAoGAZGqHmrAnbP6s1ePZZNdr
2wwGsWLhjI0Co/HeN0kyOGp6JsQBWUsxYEeKxbzD9lDQAvIozvuJUHOmzkAqW514
ayDSR3mCRQppxdILwcNJP5gtKLNV161VYl6ljpcUfV6/oyg/cvfxfDeTUgHjBtT1
yMjcDgyAejVXWSu2h9lYZMY=
-----END PRIVATE KEY-----"""
CLIENT_EMAIL = "pomogay-drive@pomogay.iam.gserviceaccount.com"
FOLDER_ID = "1huotUtxiRgN25f4I9EFoC4haEUUkVbOv"
DB_FILENAME = "pomogay.db"

def get_access_token():
    now = int(time.time())
    payload = {
        'iss': CLIENT_EMAIL,
        'scope': 'https://www.googleapis.com/auth/drive.file',
        'aud': 'https://oauth2.googleapis.com/token',
        'exp': now + 3600,
        'iat': now
    }
    signed = jwt.encode(payload, PRIVATE_KEY, algorithm='RS256')
    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=urllib.parse.urlencode({
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': signed
        }).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())['access_token']

def upload_db():
    try:
        token = get_access_token()
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', DB_FILENAME)
        if not os.path.exists(db_path):
            return False
        with open(db_path, 'rb') as f:
            data = f.read()
        q = urllib.parse.quote(f"'{FOLDER_ID}' in parents and name = '{DB_FILENAME}'")
        req = urllib.request.Request(
            f'https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)',
            headers={'Authorization': f'Bearer {token}'}
        )
        with urllib.request.urlopen(req) as r:
            files = json.loads(r.read()).get('files', [])
        if files:
            file_id = files[0]['id']
            req = urllib.request.Request(
                f'https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media',
                data=data,
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/octet-stream'},
                method='PATCH'
            )
        else:
            metadata = json.dumps({'name': DB_FILENAME, 'parents': [FOLDER_ID]})
            boundary = 'pomogay_boundary'
            body = (
                f'--{boundary}\r\nContent-Type: application/json\r\n\r\n{metadata}\r\n'
                f'--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
            req = urllib.request.Request(
                'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart',
                data=body,
                headers={'Authorization': f'Bearer {token}', 'Content-Type': f'multipart/related; boundary={boundary}'}
            )
        with urllib.request.urlopen(req) as r:
            pass
        return True
    except Exception as e:
        print(f'Ошибка сохранения: {e}')
        return False

def download_db():
    try:
        token = get_access_token()
        q = urllib.parse.quote(f"'{FOLDER_ID}' in parents and name = '{DB_FILENAME}'")
        req = urllib.request.Request(
            f'https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)',
            headers={'Authorization': f'Bearer {token}'}
        )
        with urllib.request.urlopen(req) as r:
            files = json.loads(r.read()).get('files', [])
        if not files:
            return False
        file_id = files[0]['id']
        req = urllib.request.Request(
            f'https://www.googleapis.com/drive/v3/files/{file_id}?alt=media',
            headers={'Authorization': f'Bearer {token}'}
        )
        with urllib.request.urlopen(req) as r:
            data = r.read()
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', DB_FILENAME)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with open(db_path, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:
        print(f'Ошибка загрузки: {e}')
        return False
