#!/usr/bin/env python3
"""Privatka patcher: вшивает все пользовательские названия в Telegram-Android.

Читает переменные окружения из workflow: APP_NAME, APPLICATION_ID, API_ID,
API_HASH, ICON_URL, SERVER_IP, RSA_KEY_URL.
Каждый шаг логируется; отсутствие опционального значения пропускает шаг.
"""
import glob
import os
import re
import sys
import urllib.request

APP_NAME = os.environ.get("APP_NAME", "").strip()
APPLICATION_ID = os.environ.get("APPLICATION_ID", "").strip()
API_ID = os.environ.get("API_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()
ICON_URL = os.environ.get("ICON_URL", "").strip()
SERVER_IP = os.environ.get("SERVER_IP", "").strip()
SERVER_PORT = os.environ.get("SERVER_PORT", "2398").strip()
RSA_KEY_URL = os.environ.get("RSA_KEY_URL", "").strip()
RSA_PUB = os.environ.get("RSA_PUB", "").strip()

DEFAULT_APP_ID = "org.telegram.messenger"


def log(msg):
    print(f"[patch] {msg}", flush=True)


def patch_file(path, subs, must=False):
    """subs: list of (pattern, replacement) regex pairs."""
    if not os.path.exists(path):
        if must:
            log(f"WARN: не найден обязательный файл {path}")
        return 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    original = text
    count = 0
    for pattern, repl in subs:
        text, n = re.subn(pattern, repl, text)
        count += n
    if text != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return count


def patch_app_name():
    if not APP_NAME:
        return
    total = 0
    for path in glob.glob("TMessagesProj/src/**/res/values/strings.xml", recursive=True):
        total += patch_file(path, [
            (r'(<string name="AppName">)[^<]*(</string>)', rf"\1{APP_NAME}\2"),
            (r'(<string name="AppNameBeta">)[^<]*(</string>)', rf"\1{APP_NAME}\2"),
            (r'(<string name="AppFolderName">)[^<]*(</string>)', rf"\1{APP_NAME}\2"),
        ])
    # label в AndroidManifest
    for path in glob.glob("TMessagesProj/src/**/AndroidManifest.xml", recursive=True):
        total += patch_file(path, [
            (r'(android:label=")[^"]*(/>)', rf"\g<1>{APP_NAME}\g<2>"),
        ])
    log(f"AppName -> \"{APP_NAME}\" (правок: {total})")


def patch_application_id():
    app_id = APPLICATION_ID or DEFAULT_APP_ID
    total = 0
    # APP_PACKAGE в gradle.properties — источник applicationId всех app-модулей
    # (TMessagesProj_App / _AppHuawei / _AppStandalone / _AppHockeyApp)
    total += patch_file("gradle.properties", [
        (r"(?m)^(APP_PACKAGE=).*$", rf"\g<1>{app_id}"),
    ])

    # Библиотека TMessagesProj: заменяем org.telegram.messenger, КРОМЕ namespace —
    # пакет R/BuildConfig и все импорты в коде зависят от него.
    # applicationId модулей задаётся через APP_PACKAGE, не через namespace.
    def _keep_ns(m):
        return m.group(0) if m.group(0).startswith("namespace") else app_id

    for path in (["buildVars.gradle"] + glob.glob("TMessagesProj/config/*.gradle")
                 + glob.glob("TMessagesProj/**/*.gradle", recursive=True)):
        total += patch_file(path, [
            (r"namespace\s+'org\.telegram\.messenger'|org\.telegram\.messenger", _keep_ns),
        ])
    for path in glob.glob("TMessagesProj/src/**/AndroidManifest.xml", recursive=True):
        total += patch_file(path, [
            (re.escape(DEFAULT_APP_ID + ".beta"), app_id),
        ])
    log(f"applicationId -> {app_id} (правок: {total}, namespace библиотеки сохранён)")


def patch_google_services():
    """google-services.json знает только официальные пакеты Telegram;
    process*GoogleServices падает с 'No matching client found'.
    Клиенты: org.telegram.messenger (namespace библиотеки TMessagesProj —
    он сохраняется, и плагин сверяет json с ним), новый app_id (из
    APP_PACKAGE) и его суффиксы .beta/.web (buildTypes app-модулей)."""
    import copy
    import json

    app_id = APPLICATION_ID or DEFAULT_APP_ID
    if app_id == DEFAULT_APP_ID:
        return
    patched = 0
    for path in glob.glob("TMessagesProj*/google-services.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log(f"WARN: {path} не читается ({e})")
            continue
        clients = data.get("client") or []
        if not clients:
            continue
        base = clients[0]
        for c in clients:
            pname = c.get("client_info", {}).get("android_client_info", {}).get("package_name")
            if pname == DEFAULT_APP_ID:
                base = c
                break
        new_clients = []
        for pkg in (DEFAULT_APP_ID, app_id, app_id + ".beta", app_id + ".web"):
            c = copy.deepcopy(base)
            c.setdefault("client_info", {}).setdefault("android_client_info", {})["package_name"] = pkg
            new_clients.append(c)
        data["client"] = new_clients
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        patched += 1
        log(f"{path}: clients -> {app_id} (+ .beta/.web)")
    log(f"google-services.json обновлён (файлов: {patched})")


def patch_api_credentials():
    if not API_ID or not API_HASH:
        log("API_ID/API_HASH не заданы — оставляю значения из репозитория")
        return
    total = 0
    for path in glob.glob("TMessagesProj/config/*.gradle"):
        total += patch_file(path, [
            (r'(APP_ID\s*=\s*")[^"]*(")', rf"\g<1>{API_ID}\g<2>"),
            (r'(APP_HASH\s*=\s*")[^"]*(")', rf"\g<1>{API_HASH}\g<2>"),
        ])
    # BuildVars в buildVars.gradle (формат def BuildVars / appId / appHash)
    for path in ["buildVars.gradle"]:
        total += patch_file(path, [
            (r'(APP_ID\s*=?\s*")[^"]*(")', rf"\g<1>{API_ID}\g<2>"),
            (r'(APP_HASH\s*=?\s*")[^"]*(")', rf"\g<1>{API_HASH}\g<2>"),
            (r'(appId\s+)\d+', rf"\g<1>{API_ID}"),
            (r'(appHash\s+")[^"]*(")', rf"\g<1>{API_HASH}\g<2>"),
        ])
    log(f"API_ID/API_HASH вшиты (правок: {total})")


def patch_icon():
    if not ICON_URL:
        return
    try:
        from PIL import Image
    except ImportError:
        os.system(f"{sys.executable} -m pip install --quiet Pillow")
        from PIL import Image
    src = "/tmp/privatka_icon.png"
    urllib.request.urlretrieve(ICON_URL, src)
    img = Image.open(src).convert("RGBA")
    sizes = {
        "mipmap-mdpi": 48, "mipmap-hdpi": 72, "mipmap-xhdpi": 96,
        "mipmap-xxhdpi": 144, "mipmap-xxxhdpi": 192,
    }
    names = ["ic_launcher.png", "ic_launcher_round.png", "ic_launcher_foreground.png"]
    count = 0
    for res_dir in glob.glob("TMessagesProj/src/**/res", recursive=True):
        for folder, size in sizes.items():
            target = os.path.join(res_dir, folder)
            if not os.path.isdir(target):
                continue
            resized = img.resize((size, size), Image.LANCZOS)
            for name in names:
                dest = os.path.join(target, name)
                if os.path.exists(dest) or name == "ic_launcher.png":
                    resized.save(dest)
                    count += 1
    log(f"Иконка заменена ({count} файлов)")


def patch_server():
    """Точка gramsrv: в нативном tgnet (initDatacenters) все адреса DC
    заменяются на IP сервера, IPv6 Телеграма удаляется, порт 443 -> порт gramsrv."""
    if not SERVER_IP:
        return
    port = SERVER_PORT or "2398"
    ip = re.escape(SERVER_IP)
    total = 0
    tgnet_files = (glob.glob("tgnet/*.cpp") + glob.glob("tgnet/*.h")
                   + glob.glob("TMessagesProj/jni/tgnet/*.cpp")
                   + glob.glob("TMessagesProj/jni/tgnet/*.h"))
    for path in tgnet_files:
        total += patch_file(path, [
            (r'(")149\.154\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
            (r'(")91\.108\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
            (r'(")95\.161\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
            # IPv6 Телеграма до одиночного VPS не достучится — убираем строки целиком
            (r'\n[^\n]*addAddressAndPort\("2001:[0-9a-fA-F:]+",[^\n]*\);', ""),
            # после замены IP: порт 443 -> порт MTProto gramsrv
            (rf'(addAddressAndPort\("{ip}",\s*)443', rf"\g<1>{port}"),
        ])
    cm = "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
    total += patch_file(cm, [
        (r'(")149\.154\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
        (r'(")91\.108\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
        (r'(")95\.161\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
    ])
    log(f"Адреса DC -> {SERVER_IP}:{port} (правок: {total})")


def _pem_to_pkcs1(pem: str) -> str:
    """Нормализует публичный ключ в PKCS#1 ('BEGIN RSA PUBLIC KEY').
    Деплой отдаёт X.509 ('BEGIN PUBLIC KEY') — tgnet читает только PKCS#1
    (PEM_read_bio_RSAPublicKey в Handshake.cpp)."""
    pem = pem.strip()
    if "BEGIN RSA PUBLIC KEY" in pem:
        return pem
    try:
        os.system(f"{sys.executable} -m pip install --quiet cryptography")
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_public_key(pem.encode())
        return key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.PKCS1).decode()
    except Exception as e:
        log(f"WARN: конвертация ключа в PKCS#1 не удалась ({e}) — оставляю как есть")
        return pem


def _rsa_fingerprint(pem: str) -> int:
    """Fingerprint ровно как в tgnet (Handshake.cpp / getCdnConfig):
    SHA1(TL-сериализация: string(n) + string(e)) → нижние 8 байт, little-endian.
    Алгоритм сверен с прод-ключом Телеграма: 0xd09d1d85de64fd85."""
    import base64
    import hashlib
    os.system(f"{sys.executable} -m pip install --quiet cryptography")
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_public_key(pem.encode())
    nums = key.public_numbers()

    def be(n: int) -> bytes:
        return n.to_bytes((n.bit_length() + 7) // 8, "big")

    def tl_string(buf: bytearray, data: bytes) -> None:
        if len(data) >= 254:
            buf.append(0xFE)
            buf += len(data).to_bytes(3, "little")
        else:
            buf.append(len(data))
        buf += data
        while len(buf) % 4:
            buf.append(0)

    buf = bytearray()
    tl_string(buf, be(nums.n))
    tl_string(buf, be(nums.e))
    return int.from_bytes(hashlib.sha1(bytes(buf)).digest()[-8:], "little", signed=True)


def patch_rsa():
    """Заменяет встроенный публичный ключ DC на ключ вашего сервера
    (получен с VPS после деплоя) во всех исходниках tgnet + считает fingerprint."""
    global RSA_PUB
    if not RSA_PUB and RSA_KEY_URL:
        try:
            log(f"RSA_PUB пуст — скачиваю ключ по URL: {RSA_KEY_URL}")
            with urllib.request.urlopen(RSA_KEY_URL, timeout=60) as resp:
                RSA_PUB = resp.read(65536).decode("utf-8", errors="replace")
        except Exception as e:
            log(f"WARN: не удалось скачать RSA-ключ по URL: {e}")
    if not RSA_PUB:
        log("RSA_PUB не передан — клиент останется на ключе Телеграма; "
            "подключение к приватному серверу НЕ пройдёт")
        return
    try:
        pkcs1 = _pem_to_pkcs1(RSA_PUB)
        fp = _rsa_fingerprint(pkcs1)
    except Exception as e:
        log(f"ERROR: не удалось обработать RSA-ключ ({e}) — сборка остановлена")
        sys.exit(1)
    with open("privatka_server_rsa.pub", "w") as f:
        f.write(pkcs1.rstrip() + "\n")
    log(f"RSA-ключ сервера: fingerprint 0x{fp & 0xffffffffffffffff:016x} ({fp})")

    # ключ — C-литерал в точном формате исходника: "\n" в конце каждой строки
    # (кроме последней), строки конкатенируются через перенос + отступ
    lines = [l.strip() for l in pkcs1.strip().splitlines() if l.strip()]
    lit = '\"' + '\\n"\n                    \"'.join(lines) + '\"'
    fp_lit = f"{fp & 0xffffffffffffffff:#018x}"

    total = 0
    hs_files = (glob.glob("tgnet/Handshake.cpp")
                + glob.glob("TMessagesProj/jni/tgnet/Handshake.cpp"))
    for path in hs_files:
        total += patch_file(path, [
            # 1) встроенный PEM-ключ (test и prod): header ... "-----END RSA PUBLIC KEY-----"
            # замена — callable, чтобы re.sub не интерпретировал backslash-эскейпы C-литерала
            (r'\"-----BEGIN RSA PUBLIC KEY-----\\n\"[\s\S]*?\"-----END RSA PUBLIC KEY-----\"',
             lambda m: lit),
            # 2) его fingerprint (0xb25898df208d2603 test / 0xd09d1d85de64fd85 prod)
            (r'push_back\(0x[0-9a-fA-F]+\)', f"push_back({fp_lit})"),
        ])
    if total == 0:
        log("ERROR: Handshake.cpp не найден или структура ключей изменилась — "
            "клиент не сможет доверять вашему DC; сборка остановлена")
        sys.exit(1)
    log(f"Патч RSA завершён: ключ и fingerprint вшиты в Handshake.cpp (правок: {total})")


def main():
    log(f"=== Privatka patcher: APP_NAME={APP_NAME!r} APPLICATION_ID={APPLICATION_ID!r} ===")
    patch_app_name()
    patch_application_id()
    patch_google_services()
    patch_api_credentials()
    patch_icon()
    patch_server()
    patch_rsa()
    log("Готово")


if __name__ == "__main__":
    main()
