import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin


SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_links",
        "description": (
            "Extract links from a webpage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "Page URL to analyze."
                    )
                }
            },
            "required": [
                "url"
            ]
        }
    }
}


def execute(
    url: str
):

    try:

        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent":
                    "Vox114/1.0"
            }
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        links = []

        for a in soup.find_all(
            "a",
            href=True
        ):

            href = a["href"]

            links.append({
                "text": a.get_text(
                    strip=True
                ),
                "url": urljoin(
                    url,
                    href
                )
            })

        unique_links = []

        seen = set()

        for link in links:

            if link["url"] in seen:
                continue

            seen.add(
                link["url"]
            )

            unique_links.append(
                link
            )

        return {
            "success": True,
            "url": url,
            "count": len(
                unique_links
            ),
            "links": unique_links
        }

    except Exception as e:

        return {
            "success": False,
            "url": url,
            "error": str(e)
        }