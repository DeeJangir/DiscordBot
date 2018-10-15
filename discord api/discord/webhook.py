# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2015-2017 Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

import aiohttp
import asyncio
import json
import time
import re

from . import utils
from .errors import InvalidArgument, HTTPException, Forbidden, NotFound
from .user import BaseUser, User

__all__ = ['WebhookAdapter', 'AsyncWebhookAdapter', 'RequestsWebhookAdapter', 'Webhook']

class WebhookAdapter:
    """Base class for all webhook adapters.

    Attributes
    ------------
    webhook: :class:`Webhook`
        The webhook that owns this adapter.
    """

    BASE = 'https://discordapp.com/api/v7'

    def _prepare(self, webhook):
        self._webhook_id = webhook.id
        self._webhook_token = webhook.token
        self._request_url = '{0.BASE}/webhooks/{1}/{2}'.format(self, webhook.id, webhook.token)
        self.webhook = webhook

    def request(self, verb, url, payload=None, multipart=None):
        """Actually does the request.

        Subclasses must implement this.

        Parameters
        -----------
        verb: str
            The HTTP verb to use for the request.
        url: str
            The URL to send the request to. This will have
            the query parameters already added to it, if any.
        multipart: Optional[dict]
            A dict containing multipart form data to send with
            the request. If a filename is being uploaded, then it will
            be under a ``file`` key which will have a 3-element :class:`tuple`
            denoting ``(filename, file, content_type)``.
        payload: Optional[dict]
            The JSON to send with the request, if any.
        """
        raise NotImplementedError()

    def delete_webhook(self):
        return self.request('DELETE', self._request_url)

    def edit_webhook(self, **payload):
        return self.request('PATCH', self._request_url, payload=payload)

    def handle_execution_response(self, data, *, wait):
        """Transforms the webhook execution response into something
        more meaningful.

        This is mainly used to convert the data into a :class:`Message`
        if necessary.

        Subclasses must implement this.

        Parameters
        ------------
        data
            The data that was returned from the request.
        wait: bool
            Whether the webhook execution was asked to wait or not.
        """
        raise NotImplementedError()

    def _store_user(self, data):
        # mocks a ConnectionState for appropriate use for Message
        return BaseUser(state=self, data=data)

    def execute_webhook(self, *, payload, wait=False, file=None):
        if file is not None:
            multipart = {
                'file': file,
                'payload_json': utils.to_json(payload)
            }
            data = None
        else:
            data = payload
            multipart = None

        url = '%s?wait=%d' % (self._request_url, wait)
        maybe_coro = self.request('POST', url, multipart=multipart, payload=data)
        return self.handle_execution_response(maybe_coro, wait=wait)

class AsyncWebhookAdapter(WebhookAdapter):
    """A webhook adapter suited for use with aiohttp.

    .. note::

        You are responsible for cleaning up the client session.

    Parameters
    -----------
    session: aiohttp.ClientSession
        The session to use to send requests.
    """

    def __init__(self, session):
        self.session = session
        self.loop = session.loop

    async def request(self, verb, url, payload=None, multipart=None):
        headers = {}
        data = None
        if payload:
            headers['Content-Type'] = 'application/json'
            data = utils.to_json(payload)

        if multipart:
            file = multipart.pop('file', None)
            data = aiohttp.FormData()
            if file:
                data.add_field('file', file[1], filename=file[0], content_type=file[2])
            for key, value in multipart.items():
                data.add_field(key, value)

        for tries in range(5):
            async with self.session.request(verb, url, headers=headers, data=data) as r:
                data = await r.text(encoding='utf-8')
                if r.headers['Content-Type'] == 'application/json':
                    data = json.loads(data)

                # check if we have rate limit header information
                remaining = r.headers.get('X-Ratelimit-Remaining')
                if remaining == '0' and r.status != 429:
                    delta = utils._parse_ratelimit_header(r)
                    await asyncio.sleep(delta, loop=self.loop)

                if 300 > r.status >= 200:
                    return data

                # we are being rate limited
                if r.status == 429:
                    retry_after = data['retry_after'] / 1000.0
                    await asyncio.sleep(retry_after, loop=self.loop)
                    continue

                if r.status in (500, 502):
                    await asyncio.sleep(1 + tries * 2, loop=self.loop)
                    continue

                if r.status == 403:
                    raise Forbidden(r, data)
                elif r.status == 404:
                    raise NotFound(r, data)
                else:
                    raise HTTPException(r, data)

    async def handle_execution_response(self, response, *, wait):
        data = await response
        if not wait:
            return data

        # transform into Message object
        from .message import Message
        return Message(data=data, state=self, channel=self.webhook.channel)

class RequestsWebhookAdapter(WebhookAdapter):
    """A webhook adapter suited for use with ``requests``.

    Only versions of requests higher than 2.13.0 are supported.

    Parameters
    -----------
    session: Optional[`requests.Session <http://docs.python-requests.org/en/latest/api/#requests.Session>`_]
        The requests session to use for sending requests. If not given then
        each request will create a new session. Note if a session is given,
        the webhook adapter **will not** clean it up for you. You must close
        the session yourself.
    sleep: bool
        Whether to sleep the thread when encountering a 429 or pre-emptive
        rate limit or a 5xx status code. Defaults to ``True``. If set to
        ``False`` then this will raise an :exc:`HTTPException` instead.
    """

    def __init__(self, session=None, *, sleep=True):
        import requests
        self.session = session or requests
        self.sleep = sleep

    def request(self, verb, url, payload=None, multipart=None):
        headers = {}
        data = None
        if payload:
            headers['Content-Type'] = 'application/json'
            data = utils.to_json(payload)

        if multipart is not None:
            data = {'payload_json': multipart.pop('payload_json')}

        for tries in range(5):
            r = self.session.request(verb, url, headers=headers, data=data, files=multipart)
            r.encoding = 'utf-8'
            data = r.text

            # compatibility with aiohttp
            r.status = r.status_code

            if r.headers['Content-Type'] == 'application/json':
                data = json.loads(data)

            # check if we have rate limit header information
            remaining = r.headers.get('X-Ratelimit-Remaining')
            if remaining == '0' and r.status != 429 and self.sleep:
                delta = utils._parse_ratelimit_header(r)
                time.sleep(delta)

            if 300 > r.status >= 200:
                return data

            # we are being rate limited
            if r.status == 429:
                if self.sleep:
                    retry_after = data['retry_after'] / 1000.0
                    time.sleep(retry_after)
                    continue
                else:
                    raise HTTPException(r, data)

            if self.sleep and r.status in (500, 502):
                time.sleep(1 + tries * 2)
                continue

            if r.status == 403:
                raise Forbidden(r, data)
            elif r.status == 404:
                raise NotFound(r, data)
            else:
                raise HTTPException(r, data)

    def handle_execution_response(self, response, *, wait):
        if not wait:
            return response

        # transform into Message object
        from .message import Message
        return Message(data=response, state=self, channel=self.webhook.channel)

class Webhook:
    """Represents a Discord webhook.

    Webhooks are a form to send messages to channels in Discord without a
    bot user or authentication.

    There are two main ways to use Webhooks. The first is through the ones
    received by the library such as :meth:`.Guild.webhooks` and
    :meth:`.TextChannel.webhooks`. The ones received by the library will
    automatically have an adapter bound using the library's HTTP session.
    Those webhooks will have :meth:`~.Webhook.send`, :meth:`~.Webhook.delete` and
    :meth:`~.Webhook.edit` as coroutines.

    The second form involves creating a webhook object manually without having
    it bound to a websocket connection using the :meth:`~.Webhook.from_url` or
    :meth:`~.Webhook.partial` classmethods. This form allows finer grained control
    over how requests are done, allowing you to mix async and sync code using either
    ``aiohttp`` or ``requests``.

    For example, creating a webhook from a URL and using ``aiohttp``:

    .. code-block:: python3

        from discord import Webhook, AsyncWebhookAdapter
        import aiohttp

        async def foo():
            async with aiohttp.ClientSession() as session:
                webhook = Webhook.from_url('url-here', adapter=AsyncWebhookAdapter(session))
                await webhook.send('Hello World', username='Foo')

    Or creating a webhook from an ID and token and using ``requests``:

    .. code-block:: python3

        import requests
        from discord import Webhook, RequestsWebhookAdapter

        webhook = Webhook.partial(123456, 'abcdefg', adapter=RequestsWebhookAdapter())
        webhook.send('Hello World', username='Foo')

    Attributes
    ------------
    id: :class:`int`
        The webhook's ID
    token: :class:`str`
        The authentication token of the webhook.
    guild_id: Optional[:class:`int`]
        The guild ID this webhook is for.
    channel_id: Optional[:class:`int`]
        The channel ID this webhook is for.
    user: Optional[:class:`abc.User`]
        The user this webhook was created by. If the webhook was
        received without authentication then this will be ``None``.
    name: Optional[:class:`str`]
        The default name of the webhook.
    avatar: Optional[:class:`str`]
        The default avatar of the webhook.
    """

    __slots__ = ('id', 'guild_id', 'channel_id', 'user', 'name', 'avatar',
                 'token', '_state', '_adapter')

    def __init__(self, data, *, adapter, state=None):
        self.id = int(data['id'])
        self.channel_id = utils._get_as_snowflake(data, 'channel_id')
        self.guild_id = utils._get_as_snowflake(data, 'guild_id')
        self.name = data.get('name')
        self.avatar = data.get('avatar')
        self.token = data['token']
        self._state = state
        self._adapter = adapter
        self._adapter._prepare(self)

        user = data.get('user')
        if user is None:
            self.user = None
        elif state is None:
            self.user = BaseUser(state=None, data=user)
        else:
            self.user = User(state=state, data=user)

    def __repr__(self):
        return '<Webhook id=%r>' % self.id

    @property
    def url(self):
        """Returns the webhook's url."""
        return 'https://discordapp.com/api/webhooks/{}/{}'.format(self.id, self.token)

    @classmethod
    def partial(cls, id, token, *, adapter):
        """Creates a partial :class:`Webhook`.

        A partial webhook is just a webhook object with an ID and a token.

        Parameters
        -----------
        id: int
            The ID of the webhook.
        token: str
            The authentication token of the webhook.
        adapter: :class:`WebhookAdapter`
            The webhook adapter to use when sending requests. This is
            typically :class:`AsyncWebhookAdapter` for ``aiohttp`` or
            :class:`RequestsWebhookAdapter` for ``requests``.
        """

        if not isinstance(adapter, WebhookAdapter):
            raise TypeError('adapter must be a subclass of WebhookAdapter')

        data = {
            'id': id,
            'token': token
        }

        return cls(data, adapter=adapter)

    @classmethod
    def from_url(cls, url, *, adapter):
        """Creates a partial :class:`Webhook` from a webhook URL.

        Parameters
        ------------
        url: str
            The URL of the webhook.
        adapter: :class:`WebhookAdapter`
            The webhook adapter to use when sending requests. This is
            typically :class:`AsyncWebhookAdapter` for ``aiohttp`` or
            :class:`RequestsWebhookAdapter` for ``requests``.

        Raises
        -------
        InvalidArgument
            The URL is invalid.
        """

        m = re.search(r'discordapp.com/api/webhooks/(?P<id>[0-9]{17,21})/(?P<token>[A-Za-z0-9\.\-\_]{60,68})', url)
        if m is None:
            raise InvalidArgument('Invalid webhook URL given.')
        return cls(m.groupdict(), adapter=adapter)

    @classmethod
    def from_state(cls, data, state):
        return cls(data, adapter=AsyncWebhookAdapter(session=state.http._session), state=state)

    @property
    def guild(self):
        """Optional[:class:`Guild`]: The guild this webhook belongs to.

        If this is a partial webhook, then this will always return ``None``.
        """
        return self._state and self._state._get_guild(self.guild_id)

    @property
    def channel(self):
        """Optional[:class:`TextChannel`]: The text channel this webhook belongs to.

        If this is a partial webhook, then this will always return ``None``.
        """
        guild = self.guild
        return guild and guild.get_channel(self.channel_id)

    @property
    def created_at(self):
        """Returns the webhook's creation time in UTC."""
        return utils.snowflake_time(self.id)

    @property
    def avatar_url(self):
        """Returns a friendly URL version of the avatar the webhook has.

        If the webhook does not have a traditional avatar, their default
        avatar URL is returned instead.

        This is equivalent to calling :meth:`avatar_url_as` with the
        default parameters.
        """
        return self.avatar_url_as()

    def avatar_url_as(self, *, format=None, size=1024):
        """Returns a friendly URL version of the avatar the webhook has.

        If the webhook does not have a traditional avatar, their default
        avatar URL is returned instead.

        The format must be one of 'jpeg', 'jpg', or 'png'.
        The size must be a power of 2 between 16 and 1024.

        Parameters
        -----------
        format: Optional[str]
            The format to attempt to convert the avatar to.
            If the format is ``None``, then it is equivalent to png.
        size: int
            The size of the image to display.

        Returns
        --------
        str
            The resulting CDN URL.

        Raises
        ------
        InvalidArgument
            Bad image format passed to ``format`` or invalid ``size``.
        """
        if self.avatar is None:
            # Default is always blurple apparently
            return 'https://cdn.discordapp.com/embed/avatars/0.png'

        if not utils.valid_icon_size(size):
            raise InvalidArgument("size must be a power of 2 between 16 and 1024")

        format = format or 'png'

        if format not in ('png', 'jpg', 'jpeg'):
            raise InvalidArgument("format must be one of 'png', 'jpg', or 'jpeg'.")

        return 'https://cdn.discordapp.com/avatars/{0.id}/{0.avatar}.{1}?size={2}'.format(self, format, size)

    def delete(self):
        """|maybecoro|

        Deletes this Webhook.

        If the webhook is constructed with a :class:`RequestsWebhookAdapter` then this is
        not a coroutine.

        Raises
        -------
        HTTPException
            Deleting the webhook failed.
        NotFound
            This webhook does not exist.
        Forbidden
            You do not have permissions to delete this webhook.
        """
        return self._adapter.delete_webhook()

    def edit(self, **kwargs):
        """|maybecoro|

        Edits this Webhook.

        If the webhook is constructed with a :class:`RequestsWebhookAdapter` then this is
        not a coroutine.

        Parameters
        -------------
        name: Optional[str]
            The webhook's new default name.
        avatar: Optional[bytes]
            A :term:`py:bytes-like object` representing the webhook's new default avatar.

        Raises
        -------
        HTTPException
            Editing the webhook failed.
        NotFound
            This webhook does not exist.
        Forbidden
            You do not have permissions to edit this webhook.
        """
        payload = {}

        try:
            name = kwargs['name']
        except KeyError:
            pass
        else:
            if name is not None:
                payload['name'] = str(name)
            else:
                payload['name'] = None

        try:
            avatar = kwargs['avatar']
        except KeyError:
            pass
        else:
            if avatar is not None:
                payload['avatar'] = utils._bytes_to_base64_data(avatar)
            else:
                payload['avatar'] = None

        return self._adapter.edit_webhook(**payload)

    def send(self, content=None, *, wait=False, username=None, avatar_url=None,
                                    tts=False, file=None, embed=None, embeds=None):
        """|maybecoro|

        Sends a message using the webhook.

        If the webhook is constructed with a :class:`RequestsWebhookAdapter` then this is
        not a coroutine.

        The content must be a type that can convert to a string through ``str(content)``.

        To upload a single file, the ``file`` parameter should be used with a
        single :class:`File` object.

        If the ``embed`` parameter is provided, it must be of type :class:`Embed` and
        it must be a rich embed type. You cannot mix the ``embed`` parameter with the
        ``embeds`` parameter, which must be a :class:`list` of :class:`Embed` objects to send.

        Parameters
        ------------
        content
            The content of the message to send.
        wait: bool
            Whether the server should wait before sending a response. This essentially
            means that the return type of this function changes from ``None`` to
            a :class:`Message` if set to ``True``.
        username: str
            The username to send with this message. If no username is provided
            then the default username for the webhook is used.
        avatar_url: str
            The avatar URL to send with this message. If no avatar URL is provided
            then the default avatar for the webhook is used.
        tts: bool
            Indicates if the message should be sent using text-to-speech.
        file: :class:`File`
            The file to upload.
        embed: :class:`Embed`
            The rich embed for the content to send. This cannot be mixed with
            ``embeds`` parameter.
        embeds: List[:class:`Embed`]
            A list of embeds to send with the content. Maximum of 10. This cannot
            be mixed with the ``embed`` parameter.

        Raises
        --------
        HTTPException
            Sending the message failed.
        NotFound
            This webhook was not found.
        Forbidden
            The authorization token for the webhook is incorrect.
        InvalidArgument
            You specified both ``embed`` and ``embeds`` or the length of
            ``embeds`` was invalid.

        Returns
        ---------
        Optional[:class:`Message`]
            The message that was sent.
        """

        payload = {}

        if embeds is not None and embed is not None:
            raise InvalidArgument('Cannot mix embed and embeds keyword arguments.')

        if embeds is not None:
            if len(embeds) > 10:
                raise InvalidArgument('embeds has a maximum of 10 elements.')
            payload['embeds'] = [e.to_dict() for e in embeds]

        if embed is not None:
            payload['embeds'] = [embed.to_dict()]

        if content is not None:
            payload['content'] = str(content)

        payload['tts'] = tts
        if avatar_url:
            payload['avatar_url'] = avatar_url
        if username:
            payload['username'] = username

        if file is not None:
            try:
                to_pass = (file.filename, file.open_file(), 'application/octet-stream')
                return self._adapter.execute_webhook(wait=wait, file=to_pass, payload=payload)
            finally:
                file.close()
        else:
            return self._adapter.execute_webhook(wait=wait, payload=payload)

    def execute(self, *args, **kwargs):
        """An alias for :meth:`~.Webhook.send`."""
        return self.send(*args, **kwargs)
