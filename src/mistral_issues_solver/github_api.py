import base64

import httpx


class GitHub:
    def __init__(self, owner: str, repo: str, token: str):
        self.owner = owner
        self.repo = repo
        self.base = f"https://api.github.com/repos/{owner}/{repo}"
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=60.0,
            follow_redirects=True,
        )

    async def _request(self, method: str, url: str, **kw):
        resp = await self._client.request(method, url, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {url} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else None

    async def search_issues(self, query: str) -> list[dict]:
        data = await self._request("GET", "https://api.github.com/search/issues", params={"q": query, "per_page": 10})
        return data.get("items", [])

    async def get_issue(self, number: int) -> dict:
        return await self._request("GET", f"{self.base}/issues/{number}")

    async def list_comments(self, number: int) -> list[dict]:
        return await self._request("GET", f"{self.base}/issues/{number}/comments", params={"per_page": 100})

    async def add_labels(self, number: int, labels: list[str]) -> None:
        await self._request("POST", f"{self.base}/issues/{number}/labels", json={"labels": labels})

    async def remove_label(self, number: int, label: str) -> None:
        resp = await self._client.request("DELETE", f"{self.base}/issues/{number}/labels/{label}")
        if resp.status_code not in (200, 204, 404):
            raise RuntimeError(f"GitHub DELETE label -> {resp.status_code}: {resp.text[:300]}")

    async def create_comment(self, number: int, body: str) -> None:
        await self._request("POST", f"{self.base}/issues/{number}/comments", json={"body": body})

    async def close_issue(self, number: int) -> None:
        await self._request("PATCH", f"{self.base}/issues/{number}", json={"state": "closed"})

    async def ensure_label(self, name: str, description: str, color: str) -> None:
        resp = await self._client.request(
            "POST",
            f"https://api.github.com/repos/{self.owner}/{self.repo}/labels",
            json={"name": name, "description": description, "color": color},
        )
        if resp.status_code not in (201, 422):  # 422: already exists
            raise RuntimeError(f"GitHub create label -> {resp.status_code}: {resp.text[:300]}")

    async def get_tree(self) -> list[str]:
        data = await self._request("GET", f"{self.base}/git/trees/main?recursive=1")
        return [e["path"] for e in data.get("tree", []) if e.get("type") == "blob"]

    async def code_search(self, query: str) -> list[dict]:
        data = await self._request("GET", "https://api.github.com/search/code", params={"q": query, "per_page": 5})
        return [{"path": i.get("path", ""), "name": i.get("name", "")} for i in data.get("items", [])]

    async def get_file_contents(self, path: str) -> str:
        data = await self._request("GET", f"{self.base}/contents/{path}")
        if isinstance(data, dict) and data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", "replace")
        return ""

    async def get_pr(self, number: int) -> dict:
        return await self._request("GET", f"{self.base}/pulls/{number}")

    async def create_pr(self, title: str, head: str, base: str, body: str, reviewers: list[str]) -> dict:
        return await self._request(
            "POST",
            f"{self.base}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "requested_reviewers": reviewers},
        )
