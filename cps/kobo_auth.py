#!/usr/bin/env python
# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2018-2019 shavitmichael, OzzieIsaacs
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.


"""This module is used to control authentication/authorization of Kobo sync requests.
This module also includes research notes into the auth protocol used by Kobo devices.

Log-in:
When first booting a Kobo device the user must sign into a Kobo (or affiliate) account.
Upon successful sign-in, the user is redirected to
    https://auth.kobobooks.com/CrossDomainSignIn?id=<some id>
which serves the following response:
    <script type='text/javascript'>location.href='kobo://UserAuthenticated?userId=<redacted>&userKey<redacted>&email=<redacted>&returnUrl=https%3a%2f%2fwww.kobo.com';</script>.
And triggers the insertion of a userKey into the device's User table.

Together, the device's DeviceId and UserKey act as an *irrevocable* authentication
token to most (if not all) Kobo APIs. In fact, in most cases only the UserKey is
required to authorize the API call.

Changing Kobo password *does not* invalidate user keys! This is apparently a known
issue for a few years now https://www.mobileread.com/forums/showpost.php?p=3476851&postcount=13
(although this poster hypothesised that Kobo could blacklist a DeviceId, many endpoints
will still grant access given the userkey.)

Official Kobo Store Api authorization:
* For most of the endpoints we care about (sync, metadata, tags, etc), the userKey is
passed in the x-kobo-userkey header, and is sufficient to authorize the API call.
* Some endpoints (e.g: AnnotationService) instead make use of Bearer tokens pass through
an authorization header. To get a BearerToken, the device makes a POST request to the
v1/auth/device endpoint with the secret UserKey and the device's DeviceId.
* The book download endpoint passes an auth token as a URL param instead of a header.

Our implementation:
We pretty much ignore all of the above. To authenticate the user, we generate a random
and unique token that they append to the CalibreWeb Url when setting up the api_store
setting on the device.
Thus, every request from the device to the api_store will hit CalibreWeb with the
auth_token in the url (e.g: https://mylibrary.com/<auth_token>/v1/library/sync).
In addition, once authenticated we also set the login cookie on the response that will
be sent back for the duration of the session to authorize subsequent API calls (in
particular calls to non-Kobo specific endpoints such as the CalibreWeb book download).
"""

from binascii import hexlify
from datetime import datetime
from os import urandom

from flask import g, Blueprint, url_for
from flask_login import login_user, login_required
from flask_babel import gettext as _

from . import logger, ub, lm
from .web import render_title_template

log = logger.create()


def register_url_value_preprocessor(kobo):
    @kobo.url_value_preprocessor
    def pop_auth_token(endpoint, values):
        g.auth_token = values.pop("auth_token")


def disable_failed_auth_redirect_for_blueprint(bp):
    lm.blueprint_login_views[bp.name] = None


def get_auth_token():
    if "auth_token" in g:
        return g.get("auth_token")
    else:
        return None


@lm.request_loader
def load_user_from_kobo_request(request):
    auth_token = get_auth_token()
    if auth_token is not None:
        user = (
            ub.session.query(ub.User)
            .join(ub.RemoteAuthToken)
            .filter(ub.RemoteAuthToken.auth_token == auth_token).filter(ub.RemoteAuthToken.token_type==1)
            .first()
        )
        if user is not None:
            login_user(user)
            return user
    log.info("Received Kobo request without a recognizable auth token.")
    return

kobo_auth = Blueprint("kobo_auth", __name__, url_prefix="/kobo_auth")


@kobo_auth.route("/generate_auth_token/<int:user_id>")
@login_required
def generate_auth_token(user_id):
    # Invalidate any prevously generated Kobo Auth token for this user.
    auth_token = ub.session.query(ub.RemoteAuthToken).filter(
        ub.RemoteAuthToken.user_id == user_id
    ).filter(ub.RemoteAuthToken.token_type==1).first()

    if not auth_token:
        auth_token = ub.RemoteAuthToken()
        auth_token.user_id = user_id
        auth_token.expiration = datetime.max
        auth_token.auth_token = (hexlify(urandom(16))).decode("utf-8")
        auth_token.token_type = 1

        ub.session.add(auth_token)
        ub.session.commit()

    return render_title_template(
        "generate_kobo_auth_url.html",
        title=_(u"Kobo Set-up"),
        kobo_auth_url=url_for(
            "kobo.TopLevelEndpoint", auth_token=auth_token.auth_token, _external=True
        ),
    )


@kobo_auth.route("/deleteauthtoken/<int:user_id>")
@login_required
def delete_auth_token(user_id):
    # Invalidate any prevously generated Kobo Auth token for this user.
    ub.session.query(ub.RemoteAuthToken).filter(ub.RemoteAuthToken.user_id == user_id)\
        .filter(ub.RemoteAuthToken.token_type==1).delete()
    ub.session.commit()
    return ""