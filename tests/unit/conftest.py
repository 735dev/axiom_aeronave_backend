import pytest

from app.integrations.blackbox import AttachmentVault, BlackboxClient, RetryPolicy
from tests.unit.fake_blackbox import FakeBlackbox

TOKEN = "bb_maestro_TEST"
OTHER_TOKEN = "bb_maestro_OTRA_EMPRESA"
BASE_URL = "https://blackbox.fake.invalid"
PDF = b"%PDF-1.4 contenido de prueba"


@pytest.fixture
def fake() -> FakeBlackbox:
    f = FakeBlackbox()
    f.add_token(TOKEN, company="empresa-a")
    f.add_token(OTHER_TOKEN, company="empresa-b")
    return f


@pytest.fixture
async def client(fake):
    c = BlackboxClient(BASE_URL, TOKEN, transport=fake.transport())
    yield c
    await c.aclose()


@pytest.fixture
async def make_client(fake):
    created = []

    def factory(token=TOKEN, **kw):
        c = BlackboxClient(BASE_URL, token, transport=fake.transport(), **kw)
        created.append(c)
        return c

    yield factory
    for c in created:
        await c.aclose()


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def vault(client, sleeps):
    async def fake_sleep(delay):
        sleeps.append(delay)

    return AttachmentVault(client, RetryPolicy(sleep=fake_sleep))
