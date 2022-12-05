"""Licensed under The MIT License (MIT) - Copyright (c) 2020-present James H-B. See LICENSE"""

from __future__ import annotations

import asyncio
import types
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Generic, cast, overload

from typing_extensions import NamedTuple, Self, TypeVar

from . import utils
from ._const import URL
from .app import App, PartialApp
from .enums import Language, TradeOfferState
from .errors import ClientException, ConfirmationError
from .models import CDNAsset
from .protobufs import econ
from .types.id import AppID, AssetID, ClassID, ContextID, InstanceID, TradeOfferID
from .utils import DateTime

if TYPE_CHECKING:
    from .abc import PartialUser
    from .state import ConnectionState
    from .types import trade
    from .user import ClientUser, User


__all__ = (
    "Asset",
    "Item",
    "Inventory",
    "TradeOffer",
    "MovedItem",
    "TradeOfferReceipt",
)


OwnerT = TypeVar("OwnerT", bound="PartialUser", default="User", covariant=True)


class Asset(Generic[OwnerT]):
    """Base most version of an item. This class should only be received when Steam fails to find a matching item for
    its class and instance IDs.

    .. container:: operations

        .. describe:: x == y

            Checks if two assets are equal.

        .. describe:: hash(x)

            Returns the hash of an asset.

    Attributes
    -------------
    id
        The assetid of the item.
    amount
        The amount of the same asset there are in the inventory.
    instance_id
        The instanceid of the item.
    class_id
        The classid of the item.
    post_rollback_id
        The assetid of the item after a rollback (cancelled, etc.). ``None`` if not rolled back.
    owner
        The owner of the asset
    """

    __slots__ = (
        "id",
        "amount",
        "class_id",
        "instance_id",
        # "post_rollback_id",
        "owner",
        "_app_cs",
        "_app_id",
        "_context_id",
        "_state",
    )
    REPR_ATTRS = ("id", "class_id", "instance_id", "amount", "owner", "app")  # "post_rollback_id"

    def __init__(self, state: ConnectionState, asset: econ.Asset, owner: OwnerT):
        self.id = AssetID(asset.assetid)
        self.amount = asset.amount
        self.instance_id = InstanceID(asset.instanceid)
        self.class_id = ClassID(asset.classid)
        # self.post_rollback_id = int(data["rollback_new_assetid"]) if "rollback_new_assetid" in data else None
        self.owner = owner
        self._app_id = AppID(asset.appid)
        self._context_id = ContextID(asset.contextid)
        self._state = state

    def __repr__(self) -> str:
        cls = self.__class__
        resolved = [f"{attr}={getattr(self, attr, None)!r}" for attr in cls.REPR_ATTRS]
        return f"<{cls.__name__} {' '.join(resolved)}>"

    def __eq__(self, other: Any) -> bool:
        return (
            self.id == other.id and self._app_id == other._app_id and self._context_id == other._context_id
            if isinstance(other, Asset)
            else NotImplemented
        )

    def __hash__(self) -> int:
        return hash((self.id, self._app_id, self._context_id))

    def to_dict(self) -> trade.AssetToDict:
        return {
            "assetid": str(self.id),
            "amount": self.amount,
            "appid": str(self._app_id),
            "contextid": str(self._context_id),
        }

    def to_proto(self) -> econ.Asset:
        return econ.Asset(
            assetid=self.id,
            amount=self.amount,
            instanceid=self.instance_id,
            classid=self.class_id,
            appid=self._app_id,
            contextid=self._context_id,
        )

    @utils.cached_slot_property
    def app(self) -> PartialApp:
        """The app the item is from."""
        return PartialApp(self._state, id=self._app_id, context_id=self._context_id)

    @property
    def url(self) -> str:
        """The URL for the asset in the owner's inventory.

        e.g. https://steamcommunity.com/profiles/76561198248053954/inventory/#440_2_8526584188
        """
        return f"{self.owner.community_url}/inventory#{self._app_id}_{self._context_id}_{self.id}"


class Item(Asset[OwnerT]):
    """Represents an item in a User's inventory.

    Attributes
    -------------
    name
        The market_name of the item.
    display_name
        The displayed name of the item. This could be different to :attr:`Item.name` if the item is user re-nameable.
    colour
        The colour of the item.
    descriptions
        The descriptions of the item.
    owner_descriptions
        The descriptions of the item which are visible only to the owner of the item.
    type
        The type of the item.
    tags
        The tags of the item.
    icon_url
        The icon url of the item. Uses the large (184x184 px) image url.
    fraud_warnings
        The fraud warnings for the item.
    actions
        The actions for the item.
    """

    __slots__ = (
        "name",
        "type",
        "tags",
        "colour",
        "icon",
        "display_name",
        "descriptions",
        "owner_descriptions",
        "fraud_warnings",
        "actions",
        "owner_actions",
        "market_actions",
        "_is_tradable",
        "_is_marketable",
    )
    REPR_ATTRS = ("name", *Asset.REPR_ATTRS)

    def __init__(self, state: ConnectionState, asset: econ.Asset, description: econ.ItemDescription, owner: OwnerT):
        super().__init__(state, asset, owner)

        self.name = description.market_name
        self.display_name = description.name or self.name
        self.colour = int(description.name_color, 16) if description.name_color else None
        self.descriptions = description.descriptions
        self.owner_descriptions = description.owner_descriptions
        self.type = description.type
        self.tags = description.tags
        icon_url = description.icon_url_large or description.icon_url
        self.icon = (
            CDNAsset(state, f"https://community.cloudflare.steamstatic.com/economy/image/{icon_url}")
            if icon_url
            else None
        )
        self.fraud_warnings = description.fraudwarnings
        self.actions = description.actions
        self.owner_actions = description.owner_actions
        self.market_actions = description.market_actions
        self._is_tradable = description.tradable
        self._is_marketable = description.marketable

    def is_tradable(self) -> bool:
        """Whether the item is tradable."""
        return self._is_tradable

    def is_marketable(self) -> bool:
        """Whether the item is marketable."""
        return self._is_marketable


class InventoryGenericAlias(types.GenericAlias):
    def __call__(self, *args: Any, **kwargs: Any) -> object:
        # this is done cause we need __orig_class__ in __init__
        result = self.__origin__.__new__(self.__origin__, *args, **kwargs)
        result.__orig_class__ = self
        result.__init__(*args, **kwargs)
        return result

    def __mro_entries__(self, bases: tuple[type, ...]) -> tuple[type]:
        # if we are subclassing we should return a new class that already has __orig_class__

        class BaseInventory(*super().__mro_entries__(bases)):  # type: ignore
            __slots__ = ()
            __orig_class__ = self

        return (BaseInventory,)


ItemT = TypeVar("ItemT", bound=Asset, default=Item, covariant=True)


class Inventory(Generic[ItemT, OwnerT]):
    """Represents a User's inventory.

    .. container:: operations

        .. describe:: len(x)

            Returns how many items are in the inventory.

        .. describe:: iter(x)

            Iterates over the inventory's items.

        .. describe:: x[i]

            Returns the item at the given index.

        .. describe:: y in x

            Determines if an item is in the inventory based off of its :attr:`Asset.id`.

    Attributes
    ----------
    items
        A list of the inventory's items.
    owner
        The owner of the inventory.
    app
        The app the inventory the app belongs to.
    """

    __slots__ = (
        "app",
        "items",
        "owner",
        "_language",
        "_state",
        "__orig_class__",  # undocumented typing internals more shim to make extensions work
    )
    __orig_class__: InventoryGenericAlias
    __orig_bases__: tuple[types.GenericAlias, ...]  # "duck typing"

    def __init__(
        self,
        state: ConnectionState,
        data: econ.GetInventoryItemsWithDescriptionsResponse,
        owner: OwnerT,
        app: App,
        language: Language | None,
    ):
        self._state = state
        self.owner = owner
        self.app = PartialApp(state, id=app.id, name=app.name)
        self._language = language
        self._update(data)

    def __repr__(self) -> str:
        attrs = ("owner", "app")
        resolved = [f"{attr}={getattr(self, attr)!r}" for attr in attrs]
        return f"<{self.__orig_class__} {' '.join(resolved)}>"

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[ItemT]:
        return iter(self.items)

    def __contains__(self, item: Asset) -> bool:
        if not isinstance(item, Asset):
            raise TypeError(
                f"unsupported operand type(s) for 'in': {item.__class__.__qualname__!r} and {self.__orig_class__!r}"
            )
        return item in self.items

    if not TYPE_CHECKING:
        __class_getitem__ = classmethod(InventoryGenericAlias)

    def _update(self, data: econ.GetInventoryItemsWithDescriptionsResponse) -> None:
        items: list[ItemT] = []
        try:  # ideally one day this will just be ItemT.__value__ or something
            (ItemClass,) = self.__orig_class__.__args__
        except AttributeError:
            ItemClass = self.__orig_bases__[0].__args__[0].__default__
        for asset in data.assets:
            for description in data.descriptions:
                if description.instanceid == asset.instanceid and description.classid == asset.classid:
                    items.append(ItemClass(self._state, asset=asset, description=description, owner=self.owner))
                    break
            else:
                items.append(Asset(self._state, asset=asset, owner=self.owner))  # type: ignore  # should never happen anyway
        self.items: Sequence[ItemT] = items

    async def update(self) -> None:
        """Re-fetches the inventory and updates it inplace."""
        # if self.owner == self._state.user:
        #     data = await self._state.fetch_client_user_inventory(self.app.id, self.app.context_id, self._language)
        # else:
        data = await self._state.fetch_user_inventory(self.owner.id64, self.app.id, self.app.context_id, self._language)
        self._update(data)


class TradeOfferReceipt(NamedTuple, Generic[OwnerT]):
    sent: list[MovedItem[ClientUser]]
    received: list[MovedItem[OwnerT]]


class MovedItem(Item[OwnerT]):
    """Represents an item that has moved from one inventory to another.

    Attributes
    ----------
    new_id
        The new_assetid field, this is the asset ID of the item in the partners inventory.
    new_context_id
        The new_contextid field.
    """

    __slots__ = (
        "new_id",
        "new_context_id",
    )
    REPR_ATTRS = (*Item.REPR_ATTRS, "new_id", "new_context_id")
    new_id: int
    new_context_id: int

    def __init__(self, state: ConnectionState, data: trade.TradeOfferReceiptItem, owner: OwnerT):
        super().__init__(
            state,
            asset=econ.Asset().from_dict(data),  # type: ignore  # TODO waiting on https://github.com/danielgtaylor/python-betterproto/issues/432
            description=econ.ItemDescription().from_dict(data),  # type: ignore
            owner=owner,
        )
        self.new_id = int(data["new_assetid"])
        self.new_context_id = int(data["new_contextid"])


class TradeOffer(Generic[ItemT, OwnerT]):
    """Represents a trade offer from/to send to a User.
    This can also be used in :meth:`steam.User.send`.

    Parameters
    ----------
    item_to_send
        The item to send with the trade offer. Mutually exclusive to ``items_to_send``.
    item_to_receive
        The item to receive with the trade offer. Mutually exclusive to ``items_to_receive``.
    items_to_send
        The items you are sending to the other user. Mutually exclusive to ``item_to_send``.
    items_to_receive
        The items you are receiving from the other user. Mutually exclusive to ``item_to_receive``.
    token
        The trade token used to send trades to users who aren't on the ClientUser's friend's list.
    message
         The offer message to send with the trade.

    Attributes
    ----------
    partner
        The trade offer partner. This should only ever be a :class:`steam.ID` if the partner's profile is private.
    items_to_send
        A list of items to send to the partner.
    items_to_receive
        A list of items to receive from the partner.
    state
        The offer state of the trade for the possible types see :class:`~steam.TradeOfferState`.
    message
        The message included with the trade offer.
    id
        The trade's offer ID.
    created_at
        The time at which the trade was created.
    updated_at
        The time at which the trade was last updated.
    expires
        The time at which the trade automatically expires.
    escrow
        The time at which the escrow will end. Can be ``None`` if there is no escrow on the trade.

        Warning
        -------
        This isn't likely to be accurate, use :meth:`User.escrow` instead if possible.
    """

    __slots__ = (
        "id",
        "_id",
        "state",
        "escrow",
        "partner",
        "message",
        "token",
        "expires",
        "updated_at",
        "created_at",
        "items_to_send",
        "items_to_receive",
        "_has_been_sent",
        "_state",
        "_is_our_offer",
    )

    id: int
    partner: OwnerT

    @overload
    def __init__(
        self,
        *,
        token: str | None = ...,
        message: str | None = ...,
        item_to_send: ItemT = ...,  # TODO HKT for this would be really nice as could then "ensure" we own the item
        item_to_receive: ItemT = ...,
    ):
        ...

    @overload
    def __init__(
        self,
        *,
        token: str | None = ...,
        message: str | None = ...,
        items_to_send: Sequence[ItemT],
        items_to_receive: Sequence[ItemT],
    ):
        ...

    def __init__(
        self,
        *,
        message: str | None = None,
        token: str | None = None,
        item_to_send: ItemT | None = None,
        item_to_receive: ItemT | None = None,
        items_to_send: Sequence[ItemT] | None = None,
        items_to_receive: Sequence[ItemT] | None = None,
    ):
        self.items_to_receive: Sequence[ItemT] = items_to_receive or ([item_to_receive] if item_to_receive else [])
        self.items_to_send: Sequence[ItemT] = items_to_send or ([item_to_send] if item_to_send else [])
        self.message: str | None = message or None
        self.token: str | None = token
        self.updated_at: datetime | None = None
        self.created_at: datetime | None = None
        self.escrow: timedelta | None = None
        self.state = TradeOfferState.Invalid
        self._id: int | None = None
        self._has_been_sent = False

    @classmethod
    def _from_api(cls, state: ConnectionState, data: trade.TradeOffer, partner: OwnerT) -> TradeOffer[Item[OwnerT], OwnerT]:  # type: ignore
        trade = cls()
        trade._has_been_sent = True
        trade._state = state
        trade.partner = partner
        return trade._update(data)

    @classmethod
    def _from_history(cls: type[TradeOffer[MovedItem[OwnerT], OwnerT]], state: ConnectionState, data: trade.TradeOfferHistoryTrade) -> TradeOffer[MovedItem[OwnerT], OwnerT]:  # type: ignore
        received: list[trade.TradeOfferReceiptItem] = data.get("assets_received", [])  # type: ignore  # these are updated in place so this is safe
        sent: list[trade.TradeOfferReceiptItem] = data.get("assets_given", [])  # type: ignore
        partner = cast("OwnerT", PartialUser(state, data["steamid_other"]))
        trade = cls(
            items_to_receive=[MovedItem(state, item, partner) for item in received],
            items_to_send=[MovedItem(state, item, state.user) for item in sent],
        )
        trade._state = state
        trade._id = int(data["tradeid"])
        trade.partner = partner
        trade.created_at = DateTime.from_timestamp(data["time_init"])
        trade.state = TradeOfferState.try_value(data["status"])

        return trade

    def _update_from_send(
        self, state: ConnectionState, data: trade.TradeOfferCreateResponse, partner: OwnerT, active: bool = True  # type: ignore
    ) -> None:
        self.id = int(data["tradeofferid"])
        self._state = state
        self.partner = partner
        self.state = TradeOfferState.Active if active else TradeOfferState.ConfirmationNeed
        self.created_at = DateTime.now()
        self._is_our_offer = True

    def __repr__(self) -> str:
        attrs = ("id", "state", "partner")
        resolved = [f"{attr}={getattr(self, attr, None)!r}" for attr in attrs]
        return f"<TradeOffer {' '.join(resolved)}>"

    def _update(self: TradeOffer[Asset[OwnerT], OwnerT], data: trade.TradeOffer) -> TradeOffer[Item[OwnerT], OwnerT]:
        self.message = data.get("message") or None
        self.id = int(data["tradeofferid"])
        self._id = int(data["tradeid"]) if "tradeid" in data else None
        expires = data.get("expiration_time")
        escrow = data.get("escrow_end_date")
        updated_at = data.get("time_updated")
        created_at = data.get("time_created")
        self.expires = DateTime.from_timestamp(expires) if expires else None
        self.escrow = DateTime.from_timestamp(escrow) - DateTime.now() if escrow else None
        self.updated_at = DateTime.from_timestamp(updated_at) if updated_at else None
        self.created_at = DateTime.from_timestamp(created_at) if created_at else None
        self.state = TradeOfferState.try_value(data.get("trade_offer_state", 1))
        self.items_to_send = [
            Item(
                self._state,
                asset=econ.Asset().from_dict(item),
                description=econ.ItemDescription().from_dict(item),
                owner=self._state.user,
            )
            for item in data.get("items_to_give", ())
        ]
        self.items_to_receive = [
            Item(
                self._state,
                asset=econ.Asset().from_dict(item),
                description=econ.ItemDescription().from_dict(item),
                owner=self.partner,
            )
            for item in data.get("items_to_receive", ())
        ]
        self._is_our_offer = data.get("is_our_offer", False)
        return cast("TradeOffer[Item[OwnerT], OwnerT]", self)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, TradeOffer):
            return NotImplemented
        if self._has_been_sent and other._has_been_sent:
            return self.id == other.id
        elif not (self._has_been_sent and other._has_been_sent):
            return self.items_to_send == other.items_to_send and self.items_to_receive == other.items_to_receive
        return NotImplemented

    async def confirm(self) -> None:
        """Confirms the trade offer.
        This rarely needs to be called as the client handles most of these.

        Raises
        ------
        steam.ClientException
            The trade is not active.
        steam.ConfirmationError
            No matching confirmation could not be found.
        """
        self._check_active()
        if self.is_gift():
            return  # no point trying to confirm it
        if not await self._state.fetch_and_confirm_confirmation(self.id):
            raise ConfirmationError("No matching confirmation could be found for this trade")
        self._state._confirmations.pop(self.id, None)

    async def accept(self) -> None:
        """Accepts the trade offer.

        Note
        ----
        This also calls :meth:`confirm` (if necessary) so you don't have to.

        Raises
        ------
        steam.ClientException
            The trade is either not active, already accepted or not from the ClientUser.
        steam.ConfirmationError
            No matching confirmation could not be found.
        """
        if self.state == TradeOfferState.Accepted:
            raise ClientException("This trade has already been accepted")
        if self.is_our_offer():
            raise ClientException("You cannot accept an offer the ClientUser has made")
        self._check_active()
        assert self.partner is not None
        resp = await self._state.http.accept_user_trade(self.partner.id64, self.id)
        if resp.get("needs_mobile_confirmation", False):
            for tries in range(5):
                try:
                    return await self.confirm()
                except ConfirmationError:
                    break
                except ClientException:
                    if tries == 4:
                        raise ClientException("Failed to accept trade offer") from None
                    await asyncio.sleep(tries * 2)

    async def decline(self) -> None:
        """Declines the trade offer.

        Raises
        ------
        :exc:`~steam.ClientException`
            The trade is either not active, already declined or not from the ClientUser.
        """
        if self.state == TradeOfferState.Declined:
            raise ClientException("This trade has already been declined")
        if self.is_our_offer():
            raise ClientException("You cannot decline an offer the ClientUser has made")
        self._check_active()
        await self._state.http.decline_user_trade(self.id)

    async def cancel(self) -> None:
        """Cancels the trade offer.

        Raises
        ------
        :exc:`~steam.ClientException`
            The trade is either not active or already cancelled.
        """
        if self.state == TradeOfferState.Canceled:
            raise ClientException("This trade has already been cancelled")
        self._check_active()
        await self._state.http.cancel_user_trade(self.id)

    async def receipt(self) -> TradeOfferReceipt[OwnerT]:
        """Get the receipt for a trade offer and the updated asset ids for the trade.

        Returns
        -------
        A trade receipt.

        .. source:: steam.TradeOfferReceipt
        """
        if self._id is None:
            raise ValueError("Cannot fetch the receipt for a trade not accepted")

        resp = await self._state.http.get_trade_receipt(self._id)
        data = resp["response"]
        trade = data["trades"][0]
        descriptions = data["descriptions"]
        assert self.partner is not None

        received: list[MovedItem[OwnerT]] = []
        for asset in trade.get("assets_received", ()):
            for item in descriptions:
                if item["instanceid"] == asset["instanceid"] and item["classid"] == asset["classid"]:
                    item.update(asset)
                    received.append(MovedItem(self._state, data=item, owner=self.partner))

        sent: list[MovedItem[ClientUser]] = []
        for asset in trade.get("assets_given", ()):
            for item in descriptions:
                if item["instanceid"] == asset["instanceid"] and item["classid"] == asset["classid"]:
                    item.update(asset)
                    sent.append(MovedItem(self._state, data=item, owner=self._state.user))

        return TradeOfferReceipt(sent=sent, received=received)  # type: ignore  # this might be a bug

    async def counter(self, trade: TradeOffer) -> None:
        """Counter a trade offer from an :class:`User`.

        Parameters
        -----------
        trade
            The trade offer to counter with.

        Raises
        ------
        :exc:`~steam.ClientException`
            The trade from the ClientUser or it isn't active.
        """
        self._check_active()
        if self.is_our_offer():
            raise ClientException("You cannot counter an offer the ClientUser has made")

        to_send = [item.to_dict() for item in trade.items_to_send]
        to_receive = [item.to_dict() for item in trade.items_to_receive]
        assert self.partner is not None
        resp = await self._state.http.send_trade_offer(
            self.partner, to_send, to_receive, trade.token, trade.message or "", trade_id=self.id
        )
        if resp.get("needs_mobile_confirmation", False):
            await self._state.fetch_and_confirm_confirmation(int(resp["tradeofferid"]))

    @property
    def url(self) -> str:
        """The URL of the trade offer."""
        return str(URL.COMMUNITY / f"tradeoffer/{self.id}")

    def is_gift(self) -> bool:
        """Helper method that checks if an offer is a gift to the :class:`~steam.ClientUser`"""
        return bool(self.items_to_receive and not self.items_to_send)

    def is_our_offer(self) -> bool:
        """Whether the offer was created by the :class:`~steam.ClientUser`."""
        return self._is_our_offer

    def _check_active(self) -> None:
        if self.state not in (TradeOfferState.Active, TradeOfferState.ConfirmationNeed) or not self._has_been_sent:
            raise ClientException("This trade is not active")
