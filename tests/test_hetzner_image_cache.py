import datetime
import json
import tempfile

from hetznerinstance import main


class FakeImage:
    def __init__(self, image_id: int, name: str):
        self.id = image_id
        self.name = name


class FakeClient:
    def __init__(self, image: FakeImage | None):
        self._image = image
        self.images = self

    def get_by_id(self, image_id: int) -> FakeImage:
        if self._image is None or self._image.id != image_id:
            raise RuntimeError("image not found")
        return self._image


class FakeImagesPage:
    def __init__(self, images: list[FakeImage]):
        self.images = images


class MockableClient:
    def __init__(self, images: list[FakeImage]):
        self.images = self
        self._images = images

    def get_list(self, **kwargs) -> FakeImagesPage:
        return FakeImagesPage(self._images)

    def get_by_id(self, image_id: int) -> FakeImage:
        for image in self._images:
            if image.id == image_id:
                return image
        raise RuntimeError("image not found")


def test_save_and_load_cached_latest_ubuntu():
    with tempfile.TemporaryDirectory() as tmp:
        main.CACHE_DIR = tmp
        image = FakeImage(42, "ubuntu-24.04")
        main._save_cached_latest_ubuntu("x86", image)
        client = FakeClient(image)
        loaded = main._load_cached_latest_ubuntu(client, "x86")
        assert loaded is not None
        assert loaded.id == 42
        assert loaded.name == "ubuntu-24.04"


def test_cached_latest_ubuntu_expires_after_one_week():
    with tempfile.TemporaryDirectory() as tmp:
        main.CACHE_DIR = tmp
        image = FakeImage(42, "ubuntu-24.04")
        main._save_cached_latest_ubuntu("x86", image)
        cache_path = main._latest_ubuntu_cache_path("x86")
        with open(cache_path) as f:
            data = json.load(f)
        old = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=8)
        data["cached_at"] = old.isoformat()
        with open(cache_path, "w") as f:
            json.dump(data, f)
        client = FakeClient(image)
        loaded = main._load_cached_latest_ubuntu(client, "x86")
        assert loaded is None


def test_cached_latest_ubuntu_returns_none_when_api_image_missing():
    with tempfile.TemporaryDirectory() as tmp:
        main.CACHE_DIR = tmp
        image = FakeImage(42, "ubuntu-24.04")
        main._save_cached_latest_ubuntu("x86", image)
        client = FakeClient(None)
        loaded = main._load_cached_latest_ubuntu(client, "x86")
        assert loaded is None


def test_get_image_ubuntu_returns_cached_image():
    with tempfile.TemporaryDirectory() as tmp:
        main.CACHE_DIR = tmp
        image = FakeImage(42, "ubuntu-24.04")
        main._save_cached_latest_ubuntu("x86", image)
        client = FakeClient(image)
        result = main.get_image(client, "ubuntu", "x86")
        assert result.id == 42


def test_get_image_ubuntu_fetches_and_caches_when_cache_absent():
    with tempfile.TemporaryDirectory() as tmp:
        main.CACHE_DIR = tmp
        image = FakeImage(7, "ubuntu-24.10")
        client = MockableClient([image])
        result = main.get_image(client, "ubuntu", "x86")
        assert result.id == 7
        cache_path = main._latest_ubuntu_cache_path("x86")
        with open(cache_path) as f:
            data = json.load(f)
        assert data["image_id"] == 7
        assert data["image_name"] == "ubuntu-24.10"


def test_default_image_is_ubuntu():
    assert main.DEFAULT_IMAGE == "ubuntu"
