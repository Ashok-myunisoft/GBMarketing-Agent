from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from services.mautic.oauth_service import (
    MauticOAuthError,
    MauticOAuthService,
)


class MauticError(RuntimeError):
    """Raised when a Mautic API operation fails."""


class MauticClient:
    """
    Mautic 4.x REST API client.

    Authentication is handled by MauticOAuthService.

    OAuth tokens are stored in PostgreSQL and are never
    exposed through the API.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:

        self.oauth_service = MauticOAuthService()

        self.base_url = (
            base_url
            or self.oauth_service.base_url
        ).rstrip("/")

        self.timeout = (
            timeout
            or self.oauth_service.timeout
        )

    # ============================================================
    # CONFIGURATION
    # ============================================================

    @property
    def configured(self) -> bool:
        return bool(
            self.base_url
            and self.oauth_service.client_id
            and self.oauth_service.client_secret
            and self.oauth_service.redirect_uri
        )

    # ============================================================
    # API URL
    # ============================================================

    def _api_url(self, path: str) -> str:

        return urljoin(
            f"{self.base_url}/",
            f"api/{path.lstrip('/')}",
        )

    # ============================================================
    # ACCESS TOKEN
    # ============================================================

    async def _get_access_token(self) -> str:

        token_data = (
            await self.oauth_service._get_stored_token()
        )

        if not token_data:

            raise MauticError(
                "Mautic is not connected. "
                "Connect Mautic using /mautic/connect."
            )

        access_token = token_data.get(
            "access_token"
        )

        if not access_token:

            raise MauticError(
                "Mautic access token is missing."
            )

        expires_at = token_data.get(
            "expires_at"
        )

        # --------------------------------------------------------
        # Check whether token has expired
        # --------------------------------------------------------

        if expires_at:

            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)

            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(
                    tzinfo=timezone.utc
                )

            # Refresh slightly before actual expiry
            if now >= expires_at:

                refresh_token = token_data.get(
                    "refresh_token"
                )

                if not refresh_token:

                    raise MauticError(
                        "Mautic access token expired and "
                        "no refresh token is available. "
                        "Reconnect Mautic."
                    )

                try:

                    refreshed = (
                        await self.oauth_service
                        .refresh_access_token(
                            refresh_token
                        )
                    )

                except MauticOAuthError as exc:

                    raise MauticError(
                        f"Mautic token refresh failed: {exc}"
                    ) from exc

                access_token = refreshed.get(
                    "access_token"
                )

                if not access_token:

                    raise MauticError(
                        "Mautic refresh did not return "
                        "an access token."
                    )

        return access_token

    # ============================================================
    # ERROR DETAIL
    # ============================================================

    @staticmethod
    def _error_detail(
        response: httpx.Response,
    ) -> str:

        try:

            payload = response.json()

            if isinstance(payload, dict):

                return str(
                    payload.get("error_description")
                    or payload.get("error")
                    or payload.get("message")
                    or payload
                )

        except ValueError:
            pass

        return response.text[:500]

    # ============================================================
    # REQUEST
    # ============================================================

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[
            dict[str, Any]
        ] = None,
        json: Optional[
            dict[str, Any]
        ] = None,
    ) -> dict[str, Any]:

        try:

            token = await self._get_access_token()

        except MauticError:
            raise

        headers = {
            "Authorization": (
                f"Bearer {token}"
            ),
            "Accept": "application/json",
        }

        try:

            async with httpx.AsyncClient(
                timeout=self.timeout
            ) as client:

                response = await client.request(
                    method,
                    self._api_url(path),
                    params=params,
                    json=json,
                    headers=headers,
                )

        except httpx.HTTPError as exc:

            raise MauticError(
                f"Unable to reach Mautic API: {exc}"
            ) from exc

        # --------------------------------------------------------
        # Token may have expired between our DB check
        # and the actual Mautic request.
        # --------------------------------------------------------

        if response.status_code == 401:

            token_data = (
                await self.oauth_service
                ._get_stored_token()
            )

            if not token_data:

                raise MauticError(
                    "Mautic authentication expired."
                )

            refresh_token = token_data.get(
                "refresh_token"
            )

            if not refresh_token:

                raise MauticError(
                    "Mautic returned 401 and no "
                    "refresh token is available."
                )

            try:

                refreshed = (
                    await self.oauth_service
                    .refresh_access_token(
                        refresh_token
                    )
                )

            except MauticOAuthError as exc:

                raise MauticError(
                    f"Unable to refresh Mautic token: {exc}"
                ) from exc

            new_token = refreshed.get(
                "access_token"
            )

            if not new_token:

                raise MauticError(
                    "Mautic refresh response did not "
                    "contain an access token."
                )

            headers["Authorization"] = (
                f"Bearer {new_token}"
            )

            try:

                async with httpx.AsyncClient(
                    timeout=self.timeout
                ) as client:

                    response = await client.request(
                        method,
                        self._api_url(path),
                        params=params,
                        json=json,
                        headers=headers,
                    )

            except httpx.HTTPError as exc:

                raise MauticError(
                    "Unable to reach Mautic API "
                    "after token refresh."
                ) from exc

        # --------------------------------------------------------
        # API error
        # --------------------------------------------------------

        if response.status_code >= 400:

            detail = self._error_detail(
                response
            )

            raise MauticError(
                "Mautic API request failed "
                f"({response.status_code}) "
                f"for {method} /{path}: {detail}"
            )

        # --------------------------------------------------------
        # Empty response
        # --------------------------------------------------------

        if not response.content:
            return {}

        try:

            return response.json()

        except ValueError as exc:

            raise MauticError(
                "Mautic returned a non-JSON response."
            ) from exc

    # ============================================================
    # HEALTH
    # ============================================================

    async def health(self) -> dict[str, Any]:

        payload = await self.request(
            "GET",
            "contacts",
            params={
                "limit": 1,
                "minimal": "true",
            },
        )

        return {
            "connected": True,
            "base_url": self.base_url,
            "contact_total": payload.get(
                "total"
            ),
        }

    # ============================================================
    # CONTACTS
    # ============================================================

    async def list_contacts(
        self,
        *,
        search: Optional[str] = None,
        limit: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:

        params: dict[str, Any] = {
            "limit": min(max(limit, 1), 100),
            "page": max(page, 1),
            "minimal": "true",
        }

        if search:
            params["search"] = search

        return await self.request(
            "GET",
            "contacts",
            params=params,
        )

    async def get_contact(
        self,
        contact_id: int,
    ) -> dict[str, Any]:

        return await self.request(
            "GET",
            f"contacts/{contact_id}",
        )

    async def create_contact(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:

        return await self.request(
            "POST",
            "contacts/new",
            json=data,
        )

    async def update_contact(
        self,
        contact_id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:

        return await self.request(
            "PATCH",
            f"contacts/{contact_id}/edit",
            json=data,
        )

    async def get_contact_activity(
        self,
        contact_id: int,
    ) -> dict[str, Any]:

        return await self.request(
            "GET",
            f"contacts/{contact_id}/activity",
        )

    # ============================================================
    # CAMPAIGNS
    # ============================================================

    async def add_contact_to_campaign(
        self,
        contact_id: int,
        campaign_id: int,
    ) -> dict[str, Any]:
        """
        Add a contact to a Mautic campaign.

        If the campaign is published and has a "Send Email" action on
        entry, Mautic sends that email automatically - this call does
        not send anything by itself.
        """

        return await self.request(
            "POST",
            f"campaigns/{campaign_id}/contact/{contact_id}/add",
        )
    