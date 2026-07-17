import pytest

from dbx_platform.secrets import SecretRef, parse_secret_ref


def test_parse_dbx_ref():
    assert parse_secret_ref("dbx://my-scope/api-key") == SecretRef("dbx", "my-scope", "api-key")


def test_parse_akv_ref():
    assert parse_secret_ref("akv://corp-vault/db-password") == SecretRef(
        "akv", "corp-vault", "db-password"
    )


def test_key_may_contain_slashes():
    ref = parse_secret_ref("dbx://scope/path/to/key")
    assert ref.container == "scope"
    assert ref.key == "path/to/key"


@pytest.mark.parametrize(
    "bad",
    ["", "my-scope/key", "dbx://only-scope", "dbx:///key", "akv://", "s3://bucket/key"],
)
def test_invalid_refs_rejected(bad):
    with pytest.raises(ValueError):
        parse_secret_ref(bad)
