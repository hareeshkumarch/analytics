import unittest

from app.api.openapi_export import DEFAULT_TARGET, openapi_document, render


class TestCommittedOpenApiIsCurrent(unittest.TestCase):
    def test_frontend_openapi_json_matches_the_api(self) -> None:
        self.assertEqual(
            DEFAULT_TARGET.read_text(encoding="utf-8"),
            render(openapi_document()),
            "frontend/openapi.json is out of date: run `make openapi` and commit the result.",
        )


if __name__ == "__main__":
    unittest.main()
