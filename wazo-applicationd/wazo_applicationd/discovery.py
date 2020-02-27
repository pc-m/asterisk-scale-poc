# Copyright 2020 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import asyncio
from asyncio import Queue
import consul.aio  # type: ignore
import logging
from dataclasses import dataclass

from typing import List
from typing import Union

from .config import Config
from .context import Context
from .resource import ResourceUUID

from .models.application import Application
from .models.node import ApplicationNode
from .models.service import AsteriskService

logger = logging.getLogger(__name__)

SERVICE_ID = "applicationd"


class Discovery:

    config: Config
    consul: consul.aoi.Consul

    def __init__(self, config: Config) -> None:
        self.config = config

        loop = asyncio.get_event_loop()

        self.consul = consul.aio.Consul(
            host=self.config.get("consul_host"),
            port=self.config.get("consul_port"),
            loop=loop,
        )

    async def run(self) -> None:
        logger.info("Discovery start")
        await self._register_service()

    async def register_application(self, name: str) -> Application:
        application = Application(uuid=ResourceUUID(name))
        application_uuid = application.uuid

        logger.info("Registering application {} in Consul".format(name))
        try:
            response = await self.consul.kv.put(
                "applications/{}".format(application_uuid), application_uuid
            )
            if response is not True:
                raise Exception("error", "registering app {}".format(name))

            # NOTE(safchain) do we need to have a app healthcheck ???
            """
            service_id = "apps/{}".format(application.uuid)

            response = await self.consul.agent.service.register(
                name,
                service_id=service_id,
                address=self.config.get("host"),
                port=self.config.get("port"),
            )
            if response is not True:
                raise Exception("error", "registering service {}".format(name))
            """
        except Exception as e:
            logger.error("Consul error: {}".format(e))

        return application

    async def _register_service(self) -> None:
        while True:
            try:
                response = await self.consul.agent.service.register(
                    SERVICE_ID,
                    service_id=SERVICE_ID,
                    address=self.config.get("host"),
                    port=self.config.get("port"),
                )
                if response is not True:
                    raise Exception("error", "registering service %s" % SERVICE_ID)

                status_url = "http://%s:%d/status" % (
                    self.config.get("host"),
                    self.config.get("port"),
                )
                response = await self.consul.agent.check.register(
                    SERVICE_ID,
                    consul.Check.http(status_url, "5s"),
                    service_id=SERVICE_ID,
                )
                if response is not True:
                    raise Exception("error", "registering check %s" % SERVICE_ID)

                logger.info("Service check %s registered in Consul" % SERVICE_ID)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Consul error: %s", e)

            await asyncio.sleep(5)

    async def retrieve_master_node_context(
        self, node: ApplicationNode
    ) -> Union[Context, None]:
        try:
            _, entry = await self.consul.kv.get("bridges/{}/master".format(node.uuid))
            return Context(entry["Value"].decode())
        except Exception as e:
            # TODO(safchain) need to better handling errors
            logger.debug("unable to retrieve master node {}".format(e))

        return None

    async def register_master_node(
        self, context: Context, node: ApplicationNode
    ) -> None:
        logger.info("Add node {} in Consul".format(node.uuid))
        try:
            # TODO(safchain) need put the whole context
            response = await self.consul.kv.put(
                "bridges/{}/master".format(node.uuid), context.asterisk_id
            )
            if response is not True:
                raise Exception("error")
        except Exception as e:
            logger.error("Consul error: {}".format(e))

    async def retrieve_asterisk_services(self) -> List[AsteriskService]:
        services: List[AsteriskService] = []

        try:
            _, nodes = await self.consul.health.service("asterisk")
        except Exception as e:
            logger.error("Consul error: {}".format(e))
            return services

        for node in nodes:
            service = node.get("Service", {})
            meta = service.get("Meta", {})

            id = meta.get("eid")
            address = service.get("Address")
            port = service.get("Port")

            if not id or not address or not port:
                logger.error("asterisk service definition incomplete")
                continue

            service = AsteriskService(id=id, address=address, port=port)
            services.append(service)

        return services

