import asyncio

from components.interface.reconciler.runner import run_reconciler

if __name__ == "__main__":
    asyncio.run(run_reconciler())
