# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2020 James

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import re
from datetime import datetime
from typing import TYPE_CHECKING, List
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .abc import SteamID
from .errors import HTTPException
from .iterators import CommentsIterator
from .models import URL, Comment

if TYPE_CHECKING:
    from .user import User
    from .state import ConnectionState


__all__ = (
    'Group',
)


class Group(SteamID):
    """Represents a Steam group.

    .. container:: operations

        .. describe:: x == y

            Checks if two groups are equal.

        .. describe:: x != y

            Checks if two groups are not equal.

        .. describe:: str(x)

            Returns the group's name.

        .. describe:: len(x)

            Returns the number of members in the group.


    Attributes
    ------------
    name: :class:`str`
        The name of the group.
    url: :class:`str`
        The url of the group.
    icon_url: :class:`str`
        The icon url of the group. Uses the large (184x184 px) image url.
    description: :class:`str`
        The description of the group.
    headline: :class:`str`
        The headline of the group.
    count: :class:`int`
        The amount of users in the group.
    online_count: :class:`int`
        The amount of users currently online.
    in_chat_count: :class:`int`
        The amount of currently users in the group's chat room.
    in_game_count: :class:`int`
        The amount of user's currently in game.
    """

    # TODO more to implement https://github.com/DoctorMcKay/node-steamcommunity/blob/master/components/groups.js

    def __init__(self, state: 'ConnectionState', id: int):
        self.url = f'{URL.COMMUNITY}/gid/{id}'
        self._state = state

    async def __ainit__(self) -> None:
        data = await self._state.request('GET', f'{self.url}/memberslistxml')
        try:
            tree = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            return
        for elem in tree:
            if elem.tag == 'totalPages':
                self._pages = int(elem.text)
            elif elem.tag == 'groupID64':  # we need to use the proper id64 as it doesn't convert well
                super().__init__(elem.text)
            elif elem.tag == 'groupDetails':
                for sub in elem:
                    if sub.tag == 'groupName':
                        self.name = sub.text
                    elif sub.tag == 'headline':
                        self.headline = sub.text
                    elif sub.tag == 'summary':
                        self.description = BeautifulSoup(sub.text, 'html.parser').get_text()
                    elif sub.tag == 'avatarFull':
                        self.icon_url = sub.text
                    elif sub.tag == 'memberCount':
                        self.count = int(sub.text)
                    elif sub.tag == 'membersInChat':
                        self.in_chat_count = int(sub.text)
                    elif sub.tag == 'membersInGame':
                        self.in_game_count = int(sub.text)
                    elif sub.tag == 'membersOnline':
                        self.online_count = int(sub.text)

    def __repr__(self):
        attrs = (
            'name',
        )
        resolved = [f'{attr}={getattr(self, attr)!r}' for attr in attrs]
        resolved.append(super().__repr__())
        return f"<Group {' '.join(resolved)}>"

    def __str__(self):
        return self.name

    def __len__(self):
        return self.count

    async def fetch_members(self) -> List['SteamID']:
        """|coro|
        Fetches a groups member list.

        Returns
        --------
        List[:class:`~steam.SteamID`]
            A basic list of the groups members.
            This can be a very slow operation due to
            the rate limits on this endpoint.
        """
        ret = []

        async def getter(i):
            try:
                data = await self._state.request('GET', f'{self.url}/memberslistxml?p={i + 1}')
            except HTTPException:
                await asyncio.sleep(20)
                await getter(i)
            else:
                tree = ElementTree.fromstring(data)
                for elem in tree:
                    if elem.tag == 'members':
                        for sub in elem:
                            if sub.tag == 'steamID64':
                                ret.append(SteamID(sub.text))

        for i in range(self._pages):
            await getter(i)

        return ret

    async def join(self) -> None:
        """|coro|
        Joins the :class:`Group`.
        """
        await self._state.http.join_group(self.id64)

    async def leave(self) -> None:
        """|coro|
        Leaves the :class:`Group`.
        """
        await self._state.http.leave_group(self.id64)

    async def invite(self, user: 'User'):
        """|coro|
        Invites a :class:`~steam.User` to the :class:`Group`.

        Parameters
        -----------
        user: :class:`~steam.User`
            The user to invite to the group.
        """
        await self._state.http.invite_user_to_group(user_id64=user.id64, group_id=self.id64)

    async def comment(self, content: str) -> Comment:
        """|coro|
        Post a comment to an :class:`Group`'s comment section.

        Parameters
        -----------
        content: :class:`str`
            The message to add to the group's profile.

        Returns
        -------
        :class:`~steam.Comment`
            The created comment.
        """
        resp = await self._state.http.post_comment(self.id64, 'Clan', content)
        id = int(re.findall(r'id="comment_(\d+)"', resp['comments_html'])[0])
        timestamp = datetime.utcfromtimestamp(resp['timelastpost'])
        return Comment(
            state=self._state, id=id, owner=self,
            timestamp=timestamp, content=content,
            author=self._state.client.user
        )

    def comments(self, limit=None, before: datetime = None, after: datetime = None) -> CommentsIterator:
        """An :class:`~steam.iterators.AsyncIterator` for accessing a
        :class:`~steam.Group`'s :class:`~steam.Comment` objects.

        Examples
        -----------

        Usage: ::

            async for comment in group.comments(limit=10):
                print('Author:', comment.author, 'Said:', comment.content)

        Flattening into a list: ::

            comments = await group.comments(limit=50).flatten()
            # comments is now a list of Comment

        All parameters are optional.

        Parameters
        ----------
        limit: Optional[:class:`int`]
            The maximum number of comments to search through.
            Default is ``None`` which will fetch the group's entire comments section.
        before: Optional[:class:`datetime.datetime`]
            A time to search for comments before.
        after: Optional[:class:`datetime.datetime`]
            A time to search for comments after.

        Yields
        ---------
        :class:`~steam.Comment`
            The comment with the comment information parsed.
        """
        return CommentsIterator(state=self._state, owner=self, limit=limit, before=before, after=after)
