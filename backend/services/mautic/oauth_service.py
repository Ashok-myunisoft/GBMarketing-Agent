from __future__ import annotations

import os
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import psycopg2
import psycopg2.extras

from core.config import settings


class MauticOAuthError(RuntimeError):
    """Raised when a Mautic OAuth operation fails."""


class MauticOAuthService:
    """
    Mautic OAuth 2 Authorization Code service.

    Flow:

        /mautic/connect
              ↓
        Generate OAuth state
              ↓
        Mautic authorization page
              ↓
        /mautic/callback
              ↓
        Validate state
              ↓
        Exchange code for token
              ↓
        Store token in PostgreSQL
    """

    def __init__(
        self,
        dsn: Optional[dict[str, Any] | str] = None,
    ):
        # Use exactly the same DB configuration approach
        # already used by MleadRepository.

        if dsn is not None:
            self._dsn = dsn

        elif settings.DATABASE_URL:
            self._dsn = settings.DATABASE_URL

        else:
            self._dsn = {
                "host": settings.POSTGRES_HOST,
                "port": settings.POSTGRES_PORT,
                "dbname": settings.POSTGRES_DB,
                "user": settings.POSTGRES_USER,
                "password": settings.POSTGRES_PASSWORD,
            }

        self.base_url = os.getenv(
            "MAUTIC_BASE_URL",
            "https://mail.goodbookserp.com",
        ).rstrip("/")

        self.client_id = os.getenv("MAUTIC_CLIENT_ID")
        self.client_secret = os.getenv("MAUTIC_CLIENT_SECRET")
        self.redirect_uri = os.getenv("MAUTIC_REDIRECT_URI")

        self.timeout = float(
            os.getenv("MAUTIC_TIMEOUT_SECONDS", "30")
        )

        self.authorization_url = (
            f"{self.base_url}/oauth/v2/authorize"
        )

        self.token_url = (
            f"{self.base_url}/oauth/v2/token"
        )

    # ============================================================
    # DATABASE
    # ============================================================

    def _connect(self):
        """Create PostgreSQL connection using project DB settings."""

        if isinstance(self._dsn, str):
            return psycopg2.connect(self._dsn)

        return psycopg2.connect(**self._dsn)

    # ============================================================
    # CONFIG
    # ============================================================

    def _validate_config(self):
        missing = []

        if not self.base_url:
            missing.append("MAUTIC_BASE_URL")

        if not self.client_id:
            missing.append("MAUTIC_CLIENT_ID")

        if not self.client_secret:
            missing.append("MAUTIC_CLIENT_SECRET")

        if not self.redirect_uri:
            missing.append("MAUTIC_REDIRECT_URI")

        if missing:
            raise MauticOAuthError(
                "Missing Mautic OAuth configuration: "
                + ", ".join(missing)
            )

    # ============================================================
    # CREATE OAUTH STATE
    # ============================================================

    async def _create_state(self) -> str:
        """
        Generate secure OAuth state and store it in PostgreSQL.
        """

        state = secrets.token_urlsafe(32)

        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(minutes=10)
        )

        with closing(self._connect()) as conn:
            with conn:
                with conn.cursor() as cur:

                    cur.execute(
                        """
                        INSERT INTO marketing_mautic_oauth_state
                        (
                            state,
                            expires_at
                        )
                        VALUES (%s, %s)
                        """,
                        (
                            state,
                            expires_at,
                        ),
                    )

        return state

    # ============================================================
    # AUTHORIZATION URL
    # ============================================================

    async def get_authorization_url(self) -> str:
        """
        Generate the Mautic OAuth authorization URL.
        """

        self._validate_config()

        state = await self._create_state()

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }

        return (
            f"{self.authorization_url}?"
            f"{urlencode(params)}"
        )

    # ============================================================
    # VALIDATE STATE
    # ============================================================

    async def _validate_state(
        self,
        state: str,
    ) -> bool:
        """
        Validate OAuth state.

        Conditions:

        - state exists
        - state has not been used
        - state has not expired
        """

        with closing(self._connect()) as conn:
            with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:

                cur.execute(
                    """
                    SELECT
                        id,
                        state,
                        expires_at,
                        used
                    FROM marketing_mautic_oauth_state
                    WHERE state = %s
                      AND used = FALSE
                      AND expires_at > NOW()
                    LIMIT 1
                    """,
                    (state,),
                )

                row = cur.fetchone()

        return row is not None

    # ============================================================
    # MARK STATE USED
    # ============================================================

    async def _mark_state_used(
        self,
        state: str,
    ):
        """
        Mark OAuth state as consumed.
        """

        with closing(self._connect()) as conn:
            with conn:
                with conn.cursor() as cur:

                    cur.execute(
                        """
                        UPDATE marketing_mautic_oauth_state
                        SET used = TRUE
                        WHERE state = %s
                        """,
                        (state,),
                    )

    # ============================================================
    # TOKEN EXCHANGE
    # ============================================================

    async def _exchange_code_for_token(
        self,
        code: str,
    ) -> dict[str, Any]:
        """
        Exchange Mautic authorization code for
        access_token + refresh_token.
        """

        payload = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }

        headers = {
            "Accept": "application/json",
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
        }

        try:

            async with httpx.AsyncClient(
                timeout=self.timeout
            ) as client:

                response = await client.post(
                    self.token_url,
                    data=payload,
                    headers=headers,
                )

        except httpx.HTTPError as exc:

            raise MauticOAuthError(
                f"Unable to reach Mautic token endpoint: {exc}"
            ) from exc

        if response.status_code >= 400:

            try:
                error_data = response.json()
            except Exception:
                error_data = response.text[:500]

            raise MauticOAuthError(
                "Mautic token exchange failed "
                f"({response.status_code}): "
                f"{error_data}"
            )

        try:
            token_data = response.json()
        except ValueError as exc:

            raise MauticOAuthError(
                "Mautic returned an invalid token response."
            ) from exc

        if "access_token" not in token_data:

            raise MauticOAuthError(
                "Mautic response does not contain access_token."
            )

        return token_data

    # ============================================================
    # STORE TOKEN
    # ============================================================

    async def _store_token(
        self,
        token_data: dict[str, Any],
    ):
        """
        Store Mautic OAuth token in PostgreSQL.
        """

        access_token = token_data.get(
            "access_token"
        )

        refresh_token = token_data.get(
            "refresh_token"
        )

        token_type = token_data.get(
            "token_type",
            "Bearer",
        )

        scope = token_data.get(
            "scope"
        )

        expires_in = token_data.get(
            "expires_in"
        )

        expires_at = None

        if expires_in is not None:

            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(
                    seconds=int(expires_in)
                )
            )

        with closing(self._connect()) as conn:

            with conn:

                with conn.cursor() as cur:

                    # We currently keep one active Mautic
                    # connection for this Marketing Agent.

                    cur.execute(
                        """
                        DELETE FROM
                            marketing_mautic_oauth_token
                        """
                    )

                    cur.execute(
                        """
                        INSERT INTO
                            marketing_mautic_oauth_token
                        (
                            access_token,
                            refresh_token,
                            token_type,
                            expires_at,
                            scope
                        )
                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            access_token,
                            refresh_token,
                            token_type,
                            expires_at,
                            scope,
                        ),
                    )

    # ============================================================
    # GET TOKEN
    # ============================================================

    async def _get_stored_token(self):
        """
        Retrieve the latest Mautic OAuth token.
        """

        with closing(self._connect()) as conn:

            with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:

                cur.execute(
                    """
                    SELECT
                        id,
                        access_token,
                        refresh_token,
                        token_type,
                        expires_at,
                        scope,
                        created_at,
                        updated_at
                    FROM marketing_mautic_oauth_token
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )

                row = cur.fetchone()

        if row is None:
            return None

        return dict(row)

    # ============================================================
    # CALLBACK
    # ============================================================

    async def handle_callback(
        self,
        code: str,
        state: str,
    ) -> dict[str, Any]:

        self._validate_config()

        if not code:
            raise MauticOAuthError(
                "Authorization code is missing."
            )

        if not state:
            raise MauticOAuthError(
                "OAuth state is missing."
            )

        # -----------------------------------------
        # 1. Validate state
        # -----------------------------------------

        valid = await self._validate_state(
            state
        )

        if not valid:

            raise MauticOAuthError(
                "Invalid or expired OAuth state."
            )

        # -----------------------------------------
        # 2. Exchange code
        # -----------------------------------------

        token_data = (
            await self._exchange_code_for_token(
                code
            )
        )

        # -----------------------------------------
        # 3. Store token
        # -----------------------------------------

        await self._store_token(
            token_data
        )

        # -----------------------------------------
        # 4. Mark state used
        # -----------------------------------------

        await self._mark_state_used(
            state
        )

        return {
            "success": True,
            "message": (
                "Mautic connected successfully."
            ),
            "token_type": token_data.get(
                "token_type",
                "Bearer",
            ),
        }

    # ============================================================
    # REFRESH TOKEN
    # ============================================================

    async def refresh_access_token(
        self,
        refresh_token: str,
    ) -> dict[str, Any]:

        self._validate_config()

        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }

        headers = {
            "Accept": "application/json",
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
        }

        try:

            async with httpx.AsyncClient(
                timeout=self.timeout
            ) as client:

                response = await client.post(
                    self.token_url,
                    data=payload,
                    headers=headers,
                )

        except httpx.HTTPError as exc:

            raise MauticOAuthError(
                f"Unable to refresh Mautic token: {exc}"
            ) from exc

        if response.status_code >= 400:

            raise MauticOAuthError(
                "Mautic token refresh failed "
                f"({response.status_code}): "
                f"{response.text[:500]}"
            )

        token_data = response.json()

        if "access_token" not in token_data:

            raise MauticOAuthError(
                "Refresh response does not contain access_token."
            )

        await self._store_token(
            token_data
        )

        return token_data

    # ============================================================
    # STATUS
    # ============================================================

    async def get_status(self) -> dict[str, Any]:

        token = await self._get_stored_token()

        if not token:

            return {
                "connected": False,
                "message": "Mautic is not connected.",
            }

        expires_at = token.get(
            "expires_at"
        )

        return {
            "connected": True,
            "message": "Mautic is connected.",
            "expires_at": (
                expires_at.isoformat()
                if expires_at
                else None
            ),
        }

    # ============================================================
    # DISCONNECT
    # ============================================================

    async def disconnect(self):

        with closing(self._connect()) as conn:

            with conn:

                with conn.cursor() as cur:

                    cur.execute(
                        """
                        DELETE FROM
                            marketing_mautic_oauth_token
                        """
                    )

        return {
            "success": True,
            "message": "Mautic disconnected.",
        }

