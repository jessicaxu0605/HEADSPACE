"""Run the summary refresh worker."""

import asyncio

from .service import main


asyncio.run(main())
