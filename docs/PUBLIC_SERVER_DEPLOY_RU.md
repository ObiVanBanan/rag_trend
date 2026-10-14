# Публичная бизнес-демонстрация RAG Tender (сервер с белым IP)

## До открытия порта в Интернет

- Для человека, который открывает веб-страницу, используйте HTTPS + HTTP Basic. Без HTTPS пароль будет передаваться с обратимым Base64-кодированием, а не шифрованием.
- Нельзя публиковать `8000/tcp`, `6333/tcp` (Qdrant), `5432/tcp` (PostgreSQL), `9090/tcp` (Prometheus). Публикуйте только HTTPS на 443/tcp (и 80/tcp для сертификата / redirect). SSH только для администратора.
- При `APP_ENV=production` сервис **не обслуживает** `/demo`, `/v1/match`, `/v1/demo/match`, `/docs` и `/metrics` без пароля. `/health/live` и `/health/ready` остаются открыты для проверок здоровья контейнера.
- Вход `PUBLIC_DEMO_USERNAME` и `PUBLIC_DEMO_PASSWORD` в `.env`; пароль — случайный длинный, не коммитить. Запросы с неверным паролем получают 401. На платные `POST /v1/*` действует процессный лимит `PUBLIC_DEMO_RPM` (по умолчанию 60/мин; это не распределённое rate limiting). Для каждого API вызова есть лимит `PUBLIC_DEMO_MAX_BATCH_SIZE` (по умолчанию 10).
- Всё равно контролируйте баланс провайдера и логирование. На сервере возможны дополнительные лимиты на уровне reverse proxy.

## Подготовка сервера

Linux с Docker Engine + Compose, Git LFS, Caddy. Подключите DNS-имя к публичному IP. Если домена нет, DNS-подстановки вида `А-Б-В-Г.sslip.io` могут работать, но это внешний бесплатный DNS-провайдер: сначала проверьте разрешение DNS и выпуск сертификата. Надёжнее свой домен.

```bash
git fetch origin
git switch --track origin/codex/business-demo-oct16
git lfs pull
cp .env.example .env
```

В `.env` необходимо указать:

```dotenv
OPENAI_API_KEY=...                     
DEEPSEEK_API_KEY=...
POSTGRES_PASSWORD=...
PUBLIC_DEMO_USERNAME=demo
PUBLIC_DEMO_PASSWORD=...                
PUBLIC_DEMO_RPM=60
PUBLIC_DEMO_MAX_BATCH_SIZE=10
API_PUBLISH_BIND=127.0.0.1:8000
WEB_SEARCH_ENABLED=false
```

**Не использовать буквальные `...`**: вместо них нужны реальные значения, пароль случайный. Проверьте `PRODUCT_CSV_HOST_PATH` и наличие `qdrant/steel_products_active.snapshot`, установленного через Git LFS. Для существующего внешнего `rag-observability-net`:

```bash
docker network inspect rag-observability-net >/dev/null 2>&1 || docker network create rag-observability-net
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
curl -f http://127.0.0.1:8000/health/ready
```

**Важно:** по умолчанию Docker Compose автоматически читает `docker-compose.override.yml`, который предназначен для параллельного observability-стека и имеет порт 8002. Для основного сервера используйте команду с явным `-f docker-compose.yml`. Если запускаете override, она использует `OBS_API_PUBLISH_BIND=127.0.0.1:8002` — порт также должен оставаться локальным.

**Почему `API_PUBLISH_BIND=127.0.0.1:8000`:** строка Compose `"${API_PUBLISH_BIND:-127.0.0.1:8000}:8000"` открывает порт только на loopback. При этом `API_PORT=8000` остаётся числовым параметром приложения.

## HTTPS reverse proxy с Caddy на хосте

Создать Caddyfile (например, `/etc/caddy/Caddyfile`):

```caddyfile
demo.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

Заменить `demo.example.com` на реальный домен или проверенный hostname с DNS-привязкой к серверу. Откройте 80/443 в фаерволе; Caddy получает TLS-сертификат и перенаправляет HTTP → HTTPS. Ссылки для пользователя:

- `https://demo.example.com/demo` — UI
- `https://demo.example.com/docs` — Swagger
- `https://demo.example.com/v1/match` — старый API (с Basic auth)
- `https://demo.example.com/v1/demo/match` — explainable API (с Basic auth)

При открытии UI браузер запросит имя и пароль. Пример вызова из терминала (пароль лучше получать интерактивно, а не записывать в историю):

```bash
curl --user demo -X POST https://demo.example.com/v1/demo/match \
  -H 'Content-Type: application/json' \
  --data '{"products":["Кран шаровой стальной фланцевый Ду50 Ру16"]}'
```

Проверки: неверный пароль даёт 401, верный открывает UI и API, недоступные снаружи порты 8000/6333/5432, `/health/ready` отдаёт 200, Qdrant snapshot загружен, работают запросы с реальными ключами и журналируются ошибки.

## Сравнение качества с конкурентом

Использовать одинаковый набор тендерных строк и единые критерии проверки инженерной совместимости. Сервис не должен видеть GOLD-артикулы до вывода результатов. Для исторических 20: `python scripts/export_demo20_cases.py` и `python scripts/demo_recheck.py --cases .tmp/demo20_verified.json`. Для frozen-100 использовать замороженный mapping с подтверждённым происхождением; новый benchmark нельзя собирать из результатов самого сопоставления.

Показывать два независимых итога: (1) `top1 known-positive ID hit`, (2) `technical-compatibility pass` по поддерживаемым требованиям. Вторую метрику для спорных EXTENDED-кейсов подтверждает инженер, а не автоматический rule checker.

Важно: UI score и LLM confidence не являются вероятностью правильного ответа. Стоимость, наличие, URL и RRP можно добавить после подтверждения источника и актуальности.
