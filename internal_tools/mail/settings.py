from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from common.tools.domain import DomainSettings, domain_settings


@domain_settings
class MailSettings(DomainSettings):
    domain: ClassVar[str] = "mail"

    send_directly: bool = Field(
        default=False,
        title="Отправлять письма сразу",
        description="Выключено — письма из задачи сохраняются в черновики, отправляет их человек. Включено — задача отправляет письма сама.",
    )
