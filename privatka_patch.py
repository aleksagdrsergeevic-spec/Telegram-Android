#!/usr/bin/env python3
"""Privatka patcher: вшивает все пользовательские названия в Telegram-Android.

Читает переменные окружения из workflow: APP_NAME, APPLICATION_ID, API_ID,
API_HASH, ICON_URL, SERVER_IP, RSA_KEY_URL, PROXY_LIST.
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
SERVER_PORT = os.environ.get("SERVER_PORT", "443").strip()
RSA_KEY_URL = os.environ.get("RSA_KEY_URL", "").strip()
RSA_PUB = os.environ.get("RSA_PUB", "").strip()
# SOCKS5-прокси для приложения: строки host:port:user:pass через ";".
# Нужны, чтобы приложение работало без VPN (провайдеры режут адрес сервера).
PROXY_LIST = os.environ.get("PROXY_LIST", "").strip()

JAVA_DIR = "TMessagesProj/src/main/java/org/telegram/messenger"
BROWSER_PATH = JAVA_DIR + "/browser/Browser.java"
APP_LOADER_PATH = JAVA_DIR + "/ApplicationLoader.java"
MESSAGES_PATH = JAVA_DIR + "/MessagesController.java"
PROXY_CLASS_PATH = JAVA_DIR + "/PrivatkaProxy.java"

# Класс, который включает вшитый прокси при первом запуске. Файл создаётся
# здесь, в исходниках его нет. Ротация прокси в этом форке уже есть: клиент
# сам переключается на доступный адрес, поэтому адресов может быть несколько.
PROXY_JAVA = """package org.telegram.messenger;

import android.content.Context;
import android.content.SharedPreferences;

import org.telegram.utils.proxy.ProxySettings;

import java.util.ArrayList;

/**
 * Прокси, вшитые в сборку: приложение работает без VPN.
 *
 * Провайдеры часто режут адрес сервера целиком, и без прокси клиент не
 * подключается. tgnet умеет SOCKS5: он просит прокси открыть TCP до адреса
 * датацентра, а прокси просто перекладывает поток. Адреса задаёт сборка,
 * клиент сам переключается на доступный (ротация прокси в настройках).
 *
 * Файл создаётся патчером Privatka — править его в исходниках не нужно.
 */
public final class PrivatkaProxy {

    /** Меняется вместе со списком: новая сборка применяет прокси заново. */
    private static final String VERSION = "__VERSION__";

    private static final String[] PROXIES = {
__PROXIES__
    };

    private static final String APPLIED = "privatka_proxy_version";

    private PrivatkaProxy() {
    }

    public static void apply() {
        if (PROXIES.length == 0 || VERSION.length() == 0) {
            return;
        }
        try {
            Context context = ApplicationLoader.applicationContext;
            if (context == null) {
                return;
            }
            SharedPreferences preferences = context.getSharedPreferences("mainconfig", Context.MODE_PRIVATE);
            if (VERSION.equals(preferences.getString(APPLIED, ""))) {
                // В этой сборке прокси уже включён: дальше решает пользователь.
                return;
            }
            SharedConfig.loadProxyList();
            ArrayList<SharedConfig.ProxyInfo> added = new ArrayList<>();
            for (int a = 0; a < PROXIES.length; a++) {
                SharedConfig.ProxyInfo info = parse(PROXIES[a]);
                if (info != null) {
                    added.add(SharedConfig.addProxy(info));
                }
            }
            if (added.isEmpty()) {
                return;
            }
            SharedConfig.proxyRotationEnabled = true;
            if (SharedConfig.currentProxy == null) {
                SharedConfig.currentProxy = added.get(0);
            }
            SharedPreferences.Editor editor = preferences.edit();
            editor.putBoolean("proxy_enabled", true);
            editor.putString(APPLIED, VERSION);
            SharedConfig.currentProxy.settings.toSharedPreferences(editor);
            editor.apply();
            // ротация читается из userconfing — включаем её и там
            context.getSharedPreferences("userconfing", Context.MODE_PRIVATE)
                    .edit().putBoolean("proxyRotationEnabled", true).apply();
            FileLog.d("PrivatkaProxy: прокси включён, адресов " + added.size());
        } catch (Throwable ignore) {
        }
    }

    private static SharedConfig.ProxyInfo parse(String row) {
        try {
            int first = row.indexOf(':');
            int second = first < 0 ? -1 : row.indexOf(':', first + 1);
            int third = second < 0 ? -1 : row.indexOf(':', second + 1);
            if (third < 0) {
                return null;
            }
            ProxySettings settings = ProxySettings.builder()
                    .setType(ProxySettings.Type.SOCKS5)
                    .setAddress(row.substring(0, first))
                    .setPort(Integer.parseInt(row.substring(first + 1, second)))
                    .setUser(row.substring(second + 1, third))
                    .setPassword(row.substring(third + 1))
                    .build();
            if (!settings.isValid()) {
                return null;
            }
            return new SharedConfig.ProxyInfo(settings);
        } catch (Throwable ignore) {
            return null;
        }
    }
}
"""

# Ссылки t.me открываются внутри приложения. Так же ведёт себя штатный
# Telegram: но если сервер отдаёт свой префикс ссылок, t.me перестаёт считаться
# внутренним — и подарки/каналы уезжают в браузер. Поэтому префикс фиксируем.
TME_BLOCK = """        // Privatka: ссылки t.me (включая подарки и звёздные подарки) открываются
        // внутри приложения, а не во внешнем браузере. Исключения — только те
        // адреса, которые приложение не умеет показывать само.
        if ("t.me".equals(host) || "telegram.me".equals(host) || "telegram.dog".equals(host)) {
            String privatkaPath = uri.getPath();
            privatkaPath = privatkaPath == null ? "" : privatkaPath.toLowerCase();
            if (!privatkaPath.startsWith("/iv") && !privatkaPath.startsWith("/s/")) {
                return true;
            }
        }
"""

HOST_ANCHOR = """        String host = AndroidUtilities.getHostAuthority(uri);
        host = host != null ? host.toLowerCase() : "";
"""

LINK_PREFIX_OLD = """        linkPrefix = mainPreferences.getString("linkPrefix", "t.me");"""
LINK_PREFIX_NEW = """        // Privatka: префикс ссылок всегда t.me — тогда любые ссылки на каналы и
        // подарки разбираются внутри приложения. Серверный префикс игнорируем.
        linkPrefix = "t.me";"""

CONFIG_PREFIX_OLD = """            linkPrefix = config.me_url_prefix;"""
CONFIG_PREFIX_NEW = """            // Privatka: сервер присылает свой префикс ссылок — не берём его.
            // Иначе ссылки строятся на чужой домен и уезжают в браузер.
            linkPrefix = "t.me";"""

APPLY_CALL = """        PrivatkaProxy.apply();
"""

# Название приложения в шапке списка чатов (org.telegram.ui.DialogsActivity) и
# на вводном экране (org.telegram.ui.IntroActivity) рисуется картинкой: на
# строку с названием натягивается ImageSpan с логотипом-надписью Telegram,
# поэтому замена имени в ресурсах на экране не видна — видно надпись Telegram.
# Убираем подстановку картинки, тогда остаётся текст, то есть имя из панели.
HEADER_LOGO_SPAN = ("ssb.setSpan(new ImageSpan(logoDrawable), 0, ssb.length(), "
                    "Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);")

# Где эта картинка подменяет название (пути проверены по loyldg/mytelegram-android@master;
# последний — запас на раскладку стокового форка Telegram).
WORDMARK_FILES = (
    "TMessagesProj/src/main/java/org/telegram/ui/DialogsActivity.java",
    "TMessagesProj/src/main/java/org/telegram/ui/IntroActivity.java",
    JAVA_DIR + "/DialogsActivity.java",
)

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
    # label в AndroidManifest. Раньше тут была ошибка: (android:label=")[^"]*(/>)
    # не совпадало НИКОГДА — после [^"]* оставалась закрывающая кавычка, которую
    # (/>) не покрывал. Поэтому под иконкой приложения всегда было "Telegram".
    # Ссылки вида @string/AppName не трогаем — это ресурс, а не имя.
    def _label(m):
        value = m.group(2)
        if not value.strip() or value.startswith("@"):
            return m.group(0)
        return m.group(1) + APP_NAME + m.group(3)

    for path in glob.glob("TMessagesProj/src/**/AndroidManifest.xml", recursive=True):
        total += patch_file(path, [
            (r'(android:label=")([^"]*)(")', _label),
        ])
    log(f"AppName -> \"{APP_NAME}\" (правок: {total})")


# Значения, похожие на текст, но технические — их заменять нельзя: ссылки,
# пакеты, ресурсы, интенты. Иначе приложение упадёт или собьёт навигацию.
_TECH_VALUE = re.compile(
    r"(://|market:|content:|android\.|org\.telegram|\.messenger|t\.me|"
    r"@string/|@null|\?attr|\.com\b|\.org\b|\.net\b|\.ru\b|intent:)", re.I)

# <string name="KEY">VALUE</string> и <item quantity="one">VALUE</item>.
# Атрибут name трогать нельзя: на ключи ссылается R.string.KEY в коде.
_EL_WITH_TEXT = re.compile(
    r"(<string\b[^>]*>|<item\b[^>]*>)([^<]*)(</string>|</item>)")


def _brand_value(value, name):
    """Заменяет слово Telegram в текстовом значении ресурса.
    None — значение трогать нельзя (техническое или без слова Telegram)."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if "telegram" not in text.lower():
        return None
    # одиночное «Telegram» — это имя приложения, заменяем;
    # всё, что похоже на ссылку/пакет/ресурс, оставляем как есть
    if text.lower() != "telegram" and _TECH_VALUE.search(value):
        return None
    repl = name.replace("\\", "\\\\")
    new = re.sub(r"\bTelegram\b", repl, value)
    new = re.sub(r"\bTELEGRAM\b", repl.upper(), new)
    new = re.sub(r"\btelegram\b", repl.lower(), new)
    return None if new == value else new


def patch_full_rebrand():
    """Заменяет слово «Telegram» на ваше название во всех пользовательских строках.

    Меняется ТОЛЬКО текст внутри <string>/<item> — имена ресурсов остаются
    прежними, иначе сломаются все ссылки R.string.* в коде. Технические
    значения (URL, пакеты, ресурсы, интенты) не трогаются."""
    if not APP_NAME:
        return
    paths = sorted(set(
        glob.glob("TMessagesProj/src/**/res/values*/strings.xml", recursive=True)))
    total = 0
    files = 0

    def _replace(m):
        new = _brand_value(m.group(2), APP_NAME)
        if new is None:
            return m.group(0)
        return m.group(1) + new + m.group(3)

    for path in paths:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        new_text, n = _EL_WITH_TEXT.subn(_replace, text)
        if n and new_text != text:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_text)
            total += n
            files += 1
    if total:
        log(f"Полный ребрендинг: Telegram -> \"{APP_NAME}\" "
            f"(строк: {total} в {files} файлах)")
    else:
        log("Полный ребрендинг: подходящих строк не найдено")


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

    # Huawei AGConnect: плагин agcp сверяет /client/package_name с applicationId
    # и падает 'Failed to verify AGConnect-Config'. Меняем все package_name.
    for path in glob.glob("TMessagesProj*/agconnect-services.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            log(f"WARN: {path} не читается ({e})")
            continue
        n = text.count(DEFAULT_APP_ID)
        if n:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(DEFAULT_APP_ID, app_id))
        log(f"{path}: package_name -> {app_id} (замен: {n})")


def patch_api_credentials():
    if not API_ID or not API_HASH:
        # Сервер app_id не проверяет, поэтому сборка не падает: остаются
        # официальные ключи Telegram из репозитория (APP_ID=4). Это работает,
        # но идентификатор чужой — предупреждаем и продолжаем.
        log("ВНИМАНИЕ: API_ID/API_HASH не заданы — останутся официальные ключи "
            "Telegram (APP_ID=4). Сборка продолжится, но лучше указать свои "
            "ключи с my.telegram.org.")
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
    # BuildVars.java — современные форки (в т.ч. loyldg/mytelegram-android) держат
    # ключи в Java, а не в gradle:
    #   public static int APP_ID = 4;
    #   public static String APP_HASH = "...";
    for path in glob.glob("TMessagesProj/src/**/BuildVars.java", recursive=True):
        total += patch_file(path, [
            (r'(APP_ID\s*=\s*)\d+', rf"\g<1>{API_ID}"),
            (r'(APP_HASH\s*=\s*")[^"]*(")', rf"\g<1>{API_HASH}\g<2>"),
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
    """Точка сервера: вшивает IP и порт вашего DC вместо адресов Telegram.

    Поддерживаются оба формата форков Telegram-Android:
    1) Стоковый (TelegramOrg/Telegram-Android): адреса DC (149.154.*, 91.108.*,
       95.161.*) лежат в tgnet (Connections.cpp и др.). Меняем их на IP сервера,
       IPv6 Телеграма убираем, порт 443 -> порт MTProto gramsrv.
    2) Форк MyTelegram (loyldg/mytelegram-android): адрес DC задан строкой
       '192.168.1.100', а порт — в initDatacenters()
       (TMessagesProj/jni/tgnet/ConnectionsManager.cpp): port=20443. Меняем
       строку адреса и порты 20443/20543/20643/20644 на IP и порт сервера."""
    if not SERVER_IP:
        return
    # По умолчанию — внешний порт (его публикует проброс на VPS). Внутренний
    # порт контейнера (2398) незачем вшивать в клиент: с ним APK не подключится.
    port = SERVER_PORT or "443"
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
            # форк MyTelegram: адрес DC строкой и порт в initDatacenters()
            (r'"192\.168\.1\.100"', f'"{SERVER_IP}"'),
            (r'(\bport2?\s*=\s*)(?:20443|20543|20643|20644)\b', rf"\g<1>{port}"),
        ])
    cm = "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
    total += patch_file(cm, [
        (r'(")149\.154\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
        (r'(")91\.108\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
        (r'(")95\.161\.[0-9.]+(")', rf"\g<1>{SERVER_IP}\g<2>"),
        (r'"192\.168\.1\.100"', f'"{SERVER_IP}"'),
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

    # 1) встроенный PEM-ключ: header ... "-----END RSA PUBLIC KEY-----".
    #    В стоковом tgnet таких блоков ДВА (ветки testBackend: test и prod) и
    #    release-сборка берёт prod-слот, поэтому подменять надо ВСЕ блоки, иначе
    #    клиент сопоставит наш fingerprint с чужим ключом и зациклит req_pq.
    #    Замена — callable, чтобы re.sub не интерпретировал backslash-эскейпы C-литерала.
    pem_re = (r'\"-----BEGIN RSA PUBLIC KEY-----\\n\"'
              r'[\s\S]*?\"-----END RSA PUBLIC KEY-----\"')
    # 2) fingerprint ключа сервера (0xb25898df208d2603 test / 0xd09d1d85de64fd85 prod)
    fp_re = r'push_back\(0x[0-9a-fA-F]+\)'
    tgnet = sorted(set(glob.glob("tgnet/*.cpp") + glob.glob("tgnet/*.h")
                       + glob.glob("TMessagesProj/jni/tgnet/*.cpp")
                       + glob.glob("TMessagesProj/jni/tgnet/*.h")))
    want_fp = "push_back(" + fp_lit + ")"
    our_line = lines[1] if len(lines) > 1 else ""
    total = 0
    done = []
    for path in tgnet:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        # ключ/fingerprint меняем только там, где они реально вшиты — чужие
        # push_back(0x...) в других файлах не трогаем
        do_key = "BEGIN RSA PUBLIC KEY" in text
        # fingerprint — только там, где реально лежит вектор serverPublicKeysFingerprints:
        # иначе push_back(0x...) чужого назначения получил бы наш fingerprint
        do_fp = "serverPublicKeysFingerprints" in text
        done.append((path, do_key, do_fp))
        subs = []
        if do_key:
            subs.append((pem_re, lambda m: lit))
        if do_fp:
            subs.append((fp_re, want_fp))
        if subs:
            total += patch_file(path, subs)
    if total == 0:
        log("ERROR: встроенный RSA-ключ не найден в tgnet — структура исходников "
            "изменилась, клиент не сможет доверять вашему DC; сборка остановлена")
        sys.exit(1)
    # Строгая проверка: не осталось ни одного чужого ключа или fingerprint.
    # Иначе APK молча уйдёт в бесконечный цикл req_pq на живом сервере.
    problems = []
    for path, do_key, do_fp in done:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if do_key and our_line:
            n_markers = text.count("-----BEGIN RSA PUBLIC KEY-----")
            n_ours = sum(1 for m in re.finditer(pem_re, text) if our_line in m.group(0))
            if n_ours != n_markers:
                problems.append(f"{path}: не все встроенные ключи заменены "
                                f"({n_ours} из {n_markers})")
            for m in re.finditer(pem_re, text):
                if our_line not in m.group(0):
                    problems.append(f"{path}: остался чужой RSA-ключ")
                    break
        if do_fp:
            for m in re.finditer(fp_re, text):
                if m.group(0) != want_fp:
                    problems.append(f"{path}: остался чужой fingerprint {m.group(0)}")
                    break
    if problems:
        for item in problems:
            log("ERROR: " + item)
        log("ERROR: клиент может доверять чужому ключу — сборка остановлена")
        sys.exit(1)
    log(f"Патч RSA завершён: ключ {fp_lit} вшит во все tgnet-исходники "
        f"(файлов: {len(tgnet)}, правок: {total})")


def _save(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def insert_after(path, anchor, addition, label):
    """Вставляет текст сразу после якоря обычной строкой: точки и скобки в
    якорях не приходится экранировать, в отличие от регулярных выражений."""
    if not os.path.exists(path):
        log("WARN: " + label + ": не найден " + path)
        return False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if addition.strip().splitlines()[0] in text:
        log(label + ": уже вшито")
        return True
    if anchor not in text:
        log("WARN: " + label + ": не нашёл место вставки в " + path)
        return False
    _save(path, text.replace(anchor, anchor + addition, 1))
    log(label + ": готово")
    return True


def insert_before(path, anchor, addition, label):
    if not os.path.exists(path):
        log("WARN: " + label + ": не найден " + path)
        return False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if addition.strip() in text:
        log(label + ": уже вшито")
        return True
    if anchor not in text:
        log("WARN: " + label + ": не нашёл место вставки в " + path)
        return False
    _save(path, text.replace(anchor, addition + anchor, 1))
    log(label + ": готово")
    return True


def patch_wordmarks():
    """В шапке чатов и на вводном экране видно наше название, а не логотип Telegram."""
    if not APP_NAME:
        return
    seen = 0
    changed = 0
    for path in WORDMARK_FILES:
        if not os.path.exists(path):
            continue
        seen += 1
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if HEADER_LOGO_SPAN not in text:
            log("Название вместо логотипа: в " + path + " уже так")
            continue
        _save(path, text.replace(HEADER_LOGO_SPAN, "", 1))
        log("Название вместо логотипа: " + path + " — картинка убрана")
        changed += 1
    if not seen:
        log("WARN: не нашёл DialogsActivity/IntroActivity — название останется логотипом")
    elif not changed:
        log("Название вместо логотипа: менять нечего")


def patch_internal_links():
    """Ссылки t.me открываются внутри приложения, а не в браузере."""
    insert_after(BROWSER_PATH, HOST_ANCHOR, TME_BLOCK, "Ссылки t.me")
    if not os.path.exists(MESSAGES_PATH):
        log("WARN: не найден MessagesController.java")
        return
    with open(MESSAGES_PATH, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    changed = 0
    for old, new in ((LINK_PREFIX_OLD, LINK_PREFIX_NEW), (CONFIG_PREFIX_OLD, CONFIG_PREFIX_NEW)):
        if old in text:
            text = text.replace(old, new, 1)
            changed += 1
        elif new.strip().splitlines()[-1] in text:
            pass          # уже пропатчено
        else:
            log("WARN: не нашёл присвоение linkPrefix: " + old.strip())
    if changed:
        _save(MESSAGES_PATH, text)
    log("Префикс ссылок: t.me (правок: " + str(changed) + ")")


def patch_proxy():
    """Вшивает SOCKS5-прокси: приложение работает без VPN."""
    allowed = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.:-_")
    rows = []
    for item in PROXY_LIST.split(";"):
        row = item.strip()
        if not row or row.count(":") < 3:
            continue
        if not all(ch in allowed for ch in row):
            log("WARN: пропускаю прокси со странными символами")
            continue
        rows.append(row)
    version = ""
    if rows:
        import hashlib
        version = hashlib.sha1(";".join(rows).encode("utf-8")).hexdigest()[:12]
    entries = "\n".join("        " + '"' + row + '",' for row in rows)
    if not os.path.isdir(JAVA_DIR):
        log("WARN: не нашёл java-исходники — прокси не вшит")
        return
    _save(PROXY_CLASS_PATH, PROXY_JAVA.replace("__VERSION__", version).replace("__PROXIES__", entries))
    insert_before(APP_LOADER_PATH, "        ProxyRotationController.init();",
                  APPLY_CALL, "Прокси при старте приложения")
    if rows:
        log("Прокси вшит в сборку: адресов " + str(len(rows)))
    else:
        log("Прокси не заданы — сборка без прокси (адрес появится после деплоя)")


def main():
    log(f"=== Privatka patcher: APP_NAME={APP_NAME!r} APPLICATION_ID={APPLICATION_ID!r} ===")
    patch_app_name()
    patch_wordmarks()
    patch_full_rebrand()
    patch_application_id()
    patch_google_services()
    patch_api_credentials()
    patch_icon()
    patch_server()
    patch_rsa()
    patch_proxy()
    patch_internal_links()
    log("Готово")


if __name__ == "__main__":
    main()
