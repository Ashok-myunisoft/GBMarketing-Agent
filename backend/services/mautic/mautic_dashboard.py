from __future__ import annotations

from typing import Any, Optional

from services.mautic.client import MauticClient, MauticError


class MauticDashboardService:
    """
    Service responsible for preparing Mautic data
    for the MarketingAgent dashboard.
    """

    def __init__(self, client: MauticClient):
        self.client = client

    # ============================================================
    # DASHBOARD
    # ============================================================

    async def get_dashboard(
        self,
        *,
        page: int = 1,
        limit: int = 25,
        search: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Get paginated Mautic contacts and dashboard summary.

        Parameters
        ----------
        page:
            Page number starting from 1.

        limit:
            Number of contacts per page.
            Maximum is 100.

        search:
            Optional Mautic contact search string.
        """

        # --------------------------------------------------------
        # Validate pagination
        # --------------------------------------------------------

        page = max(page, 1)
        limit = min(max(limit, 1), 100)

        # --------------------------------------------------------
        # Clean search value
        # --------------------------------------------------------

        if search is not None:
            search = search.strip()

            if not search:
                search = None

        # --------------------------------------------------------
        # Get contacts from Mautic
        # --------------------------------------------------------

        try:
            contacts_response = (
                await self.client.list_contacts(
                    search=search,
                    limit=limit,
                    page=page,
                )
            )

        except MauticError:
            raise

        # --------------------------------------------------------
        # Extract raw contacts
        # --------------------------------------------------------

        raw_contacts = contacts_response.get(
            "contacts",
            {}
        )

        contacts: list[dict[str, Any]] = []

        # --------------------------------------------------------
        # Mautic normally returns contacts as:
        #
        # {
        #     "contacts": {
        #         "123": {...},
        #         "124": {...}
        #     }
        # }
        #
        # --------------------------------------------------------

        if isinstance(raw_contacts, dict):

            for contact_id, contact in raw_contacts.items():

                if not isinstance(contact, dict):
                    continue

                # ------------------------------------------------
                # Get fields
                # ------------------------------------------------

                fields = contact.get(
                    "fields",
                    {}
                )

                if not isinstance(fields, dict):
                    fields = {}

                # ------------------------------------------------
                # Get core fields
                # ------------------------------------------------

                core_fields = fields.get(
                    "core",
                    {}
                )

                if not isinstance(core_fields, dict):
                    core_fields = {}

                # ------------------------------------------------
                # Helper function
                # ------------------------------------------------

                def get_field_value(
                    field_name: str,
                ) -> Any:

                    field = core_fields.get(
                        field_name,
                        {}
                    )

                    if isinstance(field, dict):
                        return field.get("value")

                    return None

                # ------------------------------------------------
                # Convert contact ID safely
                # ------------------------------------------------

                try:
                    numeric_contact_id = int(
                        contact_id
                    )

                except (TypeError, ValueError):
                    numeric_contact_id = contact_id

                # ------------------------------------------------
                # Build dashboard contact
                # ------------------------------------------------

                contacts.append({
                    "id": numeric_contact_id,

                    "firstname": get_field_value(
                        "firstname"
                    ),

                    "lastname": get_field_value(
                        "lastname"
                    ),

                    "email": get_field_value(
                        "email"
                    ),

                    "phone": get_field_value(
                        "phone"
                    ),

                    "company": get_field_value(
                        "company"
                    ),

                    "city": get_field_value(
                        "city"
                    ),

                    "country": get_field_value(
                        "country"
                    ),

                    "address1": get_field_value(
                        "address1"
                    ),

                    "address2": get_field_value(
                        "address2"
                    ),

                    "state": get_field_value(
                        "state"
                    ),

                    "zipcode": get_field_value(
                        "zipcode"
                    ),

                    "position": get_field_value(
                        "position"
                    ),

                    "website": get_field_value(
                        "website"
                    ),

                    "date_added": contact.get(
                        "dateAdded"
                    ),

                    "date_modified": contact.get(
                        "dateModified"
                    ),
                })

        # --------------------------------------------------------
        # Mautic total
        # --------------------------------------------------------

        total_contacts = contacts_response.get(
            "total"
        )

        # --------------------------------------------------------
        # Calculate pagination information
        # --------------------------------------------------------

        total_pages = None

        if total_contacts is not None:

            try:

                total_contacts_int = int(
                    total_contacts
                )

                total_pages = (
                    total_contacts_int + limit - 1
                ) // limit

            except (
                TypeError,
                ValueError,
            ):

                total_contacts_int = total_contacts

        else:

            total_contacts_int = None

        # --------------------------------------------------------
        # Return dashboard response
        # --------------------------------------------------------

        return {
            "connected": True,

            "summary": {
                "total_contacts": total_contacts_int,
                "contacts_on_page": len(
                    contacts
                ),
            },

            "contacts": contacts,

            "pagination": {
                "page": page,
                "limit": limit,
                "count": len(contacts),
                "total_pages": total_pages,
                "has_previous": page > 1,
                "has_next": (
                    total_pages is not None
                    and page < total_pages
                ),
            },
        }

    # ============================================================
    # CONTACT ACTIVITY
    # ============================================================

    _ACTIVITY_CATEGORIES = {
        "email.read": "opened",
        "page.hit": "clicked",
        "email.sent": "sent",
    }

    async def get_contact_activity(
        self,
        contact_id: int,
    ) -> dict[str, Any]:
        """
        Get the engagement timeline (sends, opens, link clicks, ...)
        for a single Mautic contact.
        """

        try:
            activity_response = (
                await self.client.get_contact_activity(
                    contact_id
                )
            )

        except MauticError:
            raise

        raw_events = activity_response.get("events", [])

        if not isinstance(raw_events, list):
            raw_events = []

        events: list[dict[str, Any]] = []

        for event in raw_events:

            if not isinstance(event, dict):
                continue

            event_key = event.get("event")
            label_data = event.get("eventLabel")

            if isinstance(label_data, dict):
                detail = label_data.get("label")
                url = label_data.get("href")
            else:
                detail = label_data
                url = None

            events.append({
                "type": event_key,
                "category": self._ACTIVITY_CATEGORIES.get(
                    event_key,
                    "other",
                ),
                "label": event.get("eventType"),
                "detail": detail,
                "url": url,
                "timestamp": event.get("timestamp"),
            })

        return {
            "contact_id": contact_id,
            "total": activity_response.get("total"),
            "events": events,
        }