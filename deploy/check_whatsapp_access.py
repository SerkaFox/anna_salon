"""Live BRIMOON access smoke test; does not send messages or request pairing."""
import requests


url = "https://brimoon.es/whatsapp/connect/main/"
session = requests.Session()
response = session.get(url, timeout=30)
assert response.status_code == 200
assert 'name="pin"' in response.text
assert '<img ' not in response.text
assert 'no-store' in response.headers.get('Cache-Control', '')
assert session.get(url + "qr.png", timeout=30).status_code == 403
assert session.get(url + "pairing/", timeout=30, allow_redirects=False).status_code == 302
assert session.get(url + "pairing-progress/", timeout=30).status_code == 403
print("Unauthenticated page/QR/pairing: protected; no-store enabled")

csrf = session.cookies.get("csrftoken")
assert csrf
response = session.post(url, data={"pin": "wrong-test-password", "csrfmiddlewaretoken": csrf},
                        headers={"Referer": url}, timeout=30)
assert response.status_code == 200 and "incorrecta" in response.text
response = session.post(url, data={"pin": "1234", "csrfmiddlewaretoken": csrf},
                        headers={"Referer": url}, timeout=30, allow_redirects=False)
assert response.status_code == 302
response = session.get(url, timeout=30)
assert response.status_code == 200 and 'name="pin"' not in response.text
print("Password 1234: accepted; wrong password: rejected; no account login needed")
if '<img ' in response.text:
    qr = session.get(url + "qr.png", timeout=30)
    assert qr.status_code == 200 and qr.content.startswith(b"\x89PNG")
    print("Authenticated QR: fresh PNG available")
else:
    print("Authenticated page rendered; QR not displayed (ready or initialization)")

progress = session.get(url + 'pairing-progress/', timeout=30)
assert progress.status_code == 200 and 'no-store' in progress.headers.get('Cache-Control', '')
assert 'auth_mode' in progress.json()
pairing = session.get(url + 'pairing/', timeout=30)
assert pairing.status_code == 200
assert 'pairing-progress/' in pairing.text
print('Code-linking page and live progress: available; no pairing requested')
