import os

# До импорта app.main: модульный app = create_app() не должен писать в домашнюю папку
os.environ.setdefault("MOSPROM_DB", ":memory:")
# Режим разработки: ни файла пользователей, ни токена воркера (старые тесты без входа)
os.environ["MOSPROM_USERS"] = os.path.join(os.path.dirname(__file__), "no-such-users.json")
os.environ.pop("WORKER_TOKEN", None)
