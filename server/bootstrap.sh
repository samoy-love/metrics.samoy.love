#!/usr/bin/env bash
# Первичная настройка секретов на сервере. Запускается ОДИН раз, на сервере,
# из каталога /opt/samoylove-metrics.
#
# Скрипт умышленно ничего не перезаписывает: повторный запуск с уже
# существующими .env или .htpasswd — это, скорее всего, ошибка, а не
# намерение сменить пароль. Смена пароля — явными командами из README.

set -Eeuo pipefail

APP_DIR=/opt/samoylove-metrics
HTPASSWD=/etc/nginx/.htpasswd-metrics
BASIC_USER=samoy.love

cd "$APP_DIR"

# --- Пароль администратора Grafana ---------------------------------------
if [[ -f .env ]]; then
    echo "  .env уже есть — пропускаю"
else
    pass=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)
    umask 077
    cat > .env <<EOF
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=${pass}
EOF
    echo "  .env создан, пароль Grafana: ${pass}"
fi

# --- Токен телеграм-бота для Alertmanager ---------------------------------
# Единственный секрет, который нельзя сгенерировать: его выдаёт @BotFather.
# Поэтому либо он приходит переменной окружения, либо скрипт останавливается —
# молча пропустить нельзя. Alertmanager монтирует этот файл и без него не
# стартует: стек поднялся бы с одним контейнером в перезапуске по кругу, то
# есть с мониторингом, который больше не умеет позвать.
#
#   TELEGRAM_BOT_TOKEN='...' bash server/bootstrap.sh
#
# Владелец — 65534: контейнер Alertmanager ходит от nobody, а файл лежит на
# хосте, и права 600 у root означали бы «permission denied» при чтении.
if [[ -f telegram-bot-token ]]; then
    echo "  telegram-bot-token уже есть — пропускаю"
elif [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    umask 077
    printf '%s' "$TELEGRAM_BOT_TOKEN" > telegram-bot-token
    sudo chown 65534:65534 telegram-bot-token
    echo "  telegram-bot-token создан"
else
    echo "TELEGRAM_BOT_TOKEN не задан: токен бота выдаёт @BotFather, придумать его нечем." >&2
    echo "Повторите запуск так: TELEGRAM_BOT_TOKEN='...' bash server/bootstrap.sh" >&2
    exit 1
fi

# --- Basic-auth для nginx -------------------------------------------------
# htpasswd из apache2-utils на этом хосте нет, ставить пакет ради одной
# строки не нужно: nginx проверяет пароль через crypt(3), а нужную строку
# умеет посчитать openssl.
#
# Пароль уезжает в openssl ЧЕРЕЗ stdin, а не аргументом. Аргументы процесса
# видны в /proc любому локальному пользователю всё время его жизни, так что
# `openssl passwd -apr1 "$pass"` показывал свежесгенерированный пароль наружу
# ровно в тот момент, когда его прячут в файл с правами 640.
#
# Схема — SHA-512 (-6), а не APR1 (-apr1): APR1 это MD5-crypt, оставленный
# ради совместимости с Apache, которого здесь нет. glibc-шный crypt(3), через
# который смотрит nginx, понимает $6$ без каких-либо пакетов.
if [[ -f "$HTPASSWD" ]]; then
    echo "  $HTPASSWD уже есть — пропускаю"
else
    pass=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-20)
    hash=$(printf '%s' "$pass" | openssl passwd -6 -stdin)
    echo "${BASIC_USER}:${hash}" | sudo tee "$HTPASSWD" >/dev/null
    sudo chown root:www-data "$HTPASSWD"
    sudo chmod 640 "$HTPASSWD"
    echo "  basic-auth: ${BASIC_USER} / ${pass}"
fi

# --- Каталог для ACME -----------------------------------------------------
# Отдельный каталог под проверку certbot: если направить её в каталог
# приложения, выкатка с --delete однажды снесёт пробу и продление молча
# перестанет работать.
sudo mkdir -p /var/www/metrics-acme
sudo chown www-data:www-data /var/www/metrics-acme

echo "Готово."
