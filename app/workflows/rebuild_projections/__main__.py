"""Run the projection rebuild worker."""

import asyncio

from .service import main


asyncio.run(main())
