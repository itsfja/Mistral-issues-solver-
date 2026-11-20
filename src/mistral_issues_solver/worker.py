import asyncio
import os

import mistralai.workflows as workflows


async def main() -> None:
    classes = workflows.discover_all_workflows_in_package("mistral_issues_solver")
    result = workflows.run_worker(classes, api_key=os.environ.get("MISTRAL_API_KEY"))
    if result is not None:
        await result


if __name__ == "__main__":
    asyncio.run(main())
