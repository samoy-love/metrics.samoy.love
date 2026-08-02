# samoy-monitoring

Мониторинг личной инфраструктуры samoy.love: один сервер, пять сайтов,
несколько systemd-сервисов. Prometheus собирает, Grafana показывает, всё
поднимается одной командой.

## Что поднято

| Компонент | Версия | Роль |
|---|---|---|
| Prometheus | v3.13.2 | хранилище метрик, 90 дней истории |
| Grafana | 13.1.1 | панели |
| node_exporter | v1.12.1 | CPU, память, диск, сеть, состояние systemd-юнитов |
| blackbox_exporter | v0.28.0 | доступность и время ответа сайтов |

Версии образов закреплены: `latest` на arm64 периодически уезжает вперёд
остальных архитектур, и обновление, которого никто не просил, ломало бы
мониторинг ровно тогда, когда он нужен.

## Что собирается

- **Хост** — загрузка CPU по режимам, память, свободное место по файловым
  системам, сеть, load average, аптайм.
- **Сервисы** — состояние юнитов `snakes`, `chillhub-api`, `chillhub-admin`,
  `status-agent`, `nginx`, `docker` и юнитов резервного копирования.
  `status-agent` и `*-backup` запускаются по таймеру, поэтому большую часть
  времени они не `active` — это норма, а не авария.
- **Сайты** — samoy.love, metro, launcher, snakes, status: код ответа, время
  ответа, срок действия сертификата.
- **Сам мониторинг** — метрики Prometheus и Grafana.

Чего нет и почему:

- **ChillHub.** Его пакет `metrics` — это приём продуктовой телеметрии
  лаунчера (`POST /metrics/report`) и агрегат для админки за авторизацией.
  Эндпоинта в формате Prometheus у сервисов нет, все проверенные пути отдают
  404. Живость самих сервисов видна через `node_systemd_unit_state`.
- **Snakes.** Цель заведена, но пока `down`: эндпоинт ещё отдаёт JSON, а
  `snakes.service` вдобавок запущен с `IPAddressDeny=any` и
  `IPAddressAllow=localhost`, из-за чего контейнер до него не достучится в
  принципе. Подробности — в комментарии в `prometheus/prometheus.yml`.

## Как зайти

Панель: **https://metrics.samoy.love/** (Grafana), Prometheus — по адресу
**https://metrics.samoy.love/prometheus/**.

Два рубежа: сначала basic-auth nginx, затем логин Grafana. Пароли лежат
только на сервере — в репозитории их нет и быть не должно:

- basic-auth — `/etc/nginx/.htpasswd-metrics`;
- администратор Grafana — `/opt/samoy-monitoring/.env`.

Порты наружу не публикуются: контейнеры слушают `127.0.0.1`, единственный
вход — nginx.

## Установка

Конфиг nginx живёт не здесь, а в `deploy-kit/nginx/sites/metrics.samoy.love.conf`
вместе со сниппетом `nginx/snippets/samoy-metrics-headers.conf` — deploy-kit
остаётся единственным источником правды по nginx.

```bash
# 1. Код на сервер
rsync -a --exclude .git --exclude .env ./ ubuntu@СЕРВЕР:/opt/samoy-monitoring/

# 2. Секреты (один раз; повторный запуск ничего не перезапишет)
ssh ubuntu@СЕРВЕР 'bash /opt/samoy-monitoring/server/bootstrap.sh'

# 3. Запуск
ssh ubuntu@СЕРВЕР 'cd /opt/samoy-monitoring && sudo docker compose up -d'

# 4. nginx — только через deploy-kit, руками в /etc/nginx не ходим
sudo /opt/deploy-kit/server/nginx-apply.sh \
    --app samoy-monitoring \
    --conf /opt/deploy-kit/nginx/sites/metrics.samoy.love.conf \
    --dest /etc/nginx/sites-available/metrics.samoy.love.conf --enable
```

Шаг 4 требует, чтобы уже существовали A-запись `metrics.samoy.love` и
сертификат:

```bash
sudo certbot certonly --webroot -w /var/www/metrics-acme -d metrics.samoy.love
```

Без сертификата `nginx -t` упадёт на отсутствующем файле, и `nginx-apply.sh`
честно откатит конфиг.

Автозапуск после перезагрузки обеспечивают `restart: unless-stopped` плюс
включённый `docker.service` — одного `restart` мало, если сам демон не
поднимается при старте системы.

## Как добавить цель

Правится `prometheus/prometheus.yml`, дальше:

```bash
rsync ... && ssh ... 'curl -s -X POST http://127.0.0.1:9090/prometheus/-/reload'
```

Перезапускать контейнер не нужно: Prometheus запущен с
`--web.enable-lifecycle`.

Обычный сервис на хосте:

```yaml
  - job_name: имя
    static_configs:
      - targets: ["host.docker.internal:ПОРТ"]
```

Сервис должен принимать подключения с адреса docker-моста: если у его юнита
стоит `IPAddressAllow=localhost`, цель будет вечно `down` (см. Snakes).

Ещё один сайт для проверки доступности — достаточно дописать URL в
`static_configs` джоба `blackbox-http`, релейблинг уже настроен.

## Где лежат данные

Именованные тома Docker, а не каталоги в репозитории: история должна
пережить полное пересоздание контейнеров, в том числе со сменой версии
образа.

| Том | Что внутри | Резерв |
|---|---|---|
| `samoy-monitoring_prometheus-data` | TSDB, 90 дней или 8 ГБ | — |
| `samoy-monitoring_grafana-data` | база Grafana | — |

Дашборд и источник данных в этой базе не хранятся: они провижинятся из
`grafana/` при каждом старте, поэтому потеря тома Grafana не потеряет панели.

Резервных копий метрик нет намеренно: это оперативные данные, восстанавливать
их неоткуда и незачем.

## Ограничения ресурсов

Сервер маленький, и мониторинг не должен становиться причиной аварии, о
которой он же и должен предупреждать. Лимиты заданы в `docker-compose.yml`:
Prometheus — 1 CPU / 1 ГБ, Grafana — 1 CPU / 768 МБ, экспортёры — по 0.5 CPU
/ 256 МБ. Логи контейнеров ротируются (3 файла по 10 МБ).
