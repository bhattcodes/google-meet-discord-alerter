#!/usr/bin/python3

# Since Google's switch from Hangouts to Google Chat,
# the endpoints and behaviour of codes has chasnged.
# This should work with the latest version.
#
# Since: 2020-10-01T00:00Z

# Expected exit codes:
# 0:    Meeting space successfully resolved
# 1:    Exception thrown
# 6:    Lookup code did not resolve to a meeting
# 7:    Meeting does not exist
# 8:    Meeting space ended
# 9:    Meeting space is private
# 128:  Incorrect invocation

import sys
from requests import get, post
from urllib.parse import urlparse
from base64 import b64decode
from re import match, findall
from os import getenv
from time import time
from http.cookies import SimpleCookie
from hashlib import sha1


class RequestError(Exception):
    def __init__(self, msg, rl, rh, rd, r):
        self.msg = msg
        self.rl = rl
        self.rh = rh
        self.rd = rd
        self.r = r
        print("REQUEST:")
        print(' '.join(['POST', repr(rl)]))
        print(repr(rh))
        print(repr(rd))

        print()

        print("RESPONSE:")
        print(repr(r), r.status_code)
        print(repr(r.headers))
        print(repr(r.text))

        print()
        super().__init__(self.msg)


def generate_sapisidhash():
    cookie = SimpleCookie()
    cookie.load(getenv('COOKIE'))
    cookie = dict(cookie.items())
    t = str(int(time()))
    return ' '.join([
        "SAPISIDHASH",
        '_'.join([
            t,
            sha1(' '.join([
                t,
                cookie['SAPISID'].coded_value,
                'https://meet.google.com'
            ]).encode()).hexdigest()
        ])
    ])


def get_requestdata_template(code):
    idtype = None
    dataformat = None
    if match(r"^([a-z]{3}-[a-z]{4}-[a-z]{3})$", code):
        # Meeting code provided
        idtype = "MEETING_CODE"
        dataformat = "\n\x0ca{0}\x30\x01"
        # Protocol Buffers: https://developers.google.com/protocol-buffers/docs/encoding
    elif match(r"^([a-zA-Z0-9]+)$", code):
        # (likely) Lookup code provided
        idtype = "LOOKUP_CODE"
        dataformat = "{0}\"\x02CA"
    else:
        print("Unrecognized identifier.")
        exit(128)
    return idtype, dataformat


def validate_meeting_code(code):
    idtype = get_requestdata_template(code)[0]
    if idtype != "LOOKUP_CODE":
        return
    rl = "https://meet.google.com/lookup/%s?authuser=%s" % (code, getenv('COOKIE_AUTHUSER'))
    rh = {
        "cookie": getenv('COOKIE'),
        "user-agent": getenv("CLIENT_USER_AGENT")
    }
    r = get(
        rl,
        headers=rh,
        allow_redirects=False
    )
    if r.status_code != 302:
        raise RequestError("Meeting code validation returned unexpected %s status code." % r.status_code, rl, rh, '', r)
    if urlparse(r.headers['Location']).path.split('/')[1:3] == ['_meet', 'whoops']:
        return False
    else:
        return True


def resolve_meeting_space(code):
    # print(repr(get_requestdata_template(code)[1].format(code)))
    rl = "https://meet.google.com/$rpc/google.rtc.meetings.v1.MeetingSpaceService/ResolveMeetingSpace"
    rh = {
        "content-type": "application/x-protobuf",
        "cookie": getenv('COOKIE'),
        "authorization": generate_sapisidhash(),
        "x-goog-api-key": getenv('GAPIKEY'),
        "x-goog-authuser": getenv('COOKIE_AUTHUSER'),
        "x-goog-encode-response-if-executable": "base64",
        "x-origin": "https://meet.google.com"
    }
    rd = get_requestdata_template(code)[1].format(code)
    r = post(
        rl,
        headers=rh,
        data=rd
    )

    if r.status_code != 200:
        # print(repr(r.status_code), repr(r.text))
        if r.status_code == 401 and "Request had invalid authentication credentials." in r.text:
            raise RequestError("Authentication failed.", rl, rh, rd, r)
        if r.status_code == 403 and "The request is missing a valid API key." in r.text:
            raise RequestError("API key invalid.", rl, rh, rd, r)
        if r.status_code == 400 and "Request contains an invalid argument." in r.text:
            raise RequestError("Invalid argument during request.", rl, rh, rd, r)
        if r.status_code == 400 and "The conference is gone" in r.text:
            print("Meeting space ended.")
            exit(8)
        if r.status_code == 404 and "Requested meeting space does not exist." in r.text:
            print("No such meeting code.")
            exit(7)
        if r.status_code == 403 and "The requester cannot resolve this meeting" in r.text:
            exit(9)
        raise RequestError("Unknown error.", rl, rh, rd, r)

    ret = b64decode(r.text).strip().split(b'\n')
    (spacecode, meetcode, meeturl) = tuple(
        map(
            bytes.decode,
            tuple(
                findall(
                    r"^"                                    # The beginning of the line
                    r".*?(spaces\/[A-Za-z0-9\\._]{12})"     # Group 1: The space code that uses base64
                    r".*?([a-z]{3}-[a-z]{4}-[a-z]{3})"      # Group 2: The Meet code, using the format xxx-xxxx-xxx
                    r".*?(\$"                               # Group 3: The resolved URL for the Meet (starts with $) 
                    r"https?:\/\/(?:\w*\.)?"                # Protocol and subdomain, if one exists
                    r"meet\.google\.com\/"                  # Hostname (domain), which must be meet.google.com
                    r"[\^\-\\\]\_\.\~\!\*\'\(\)\;\:\@\&\=\+\$\,\/\?\%\#A-z0-9]*?)"  # Valid characters in URL path
                    r".*?$"
                        .replace('.*?', '\x13', 1)          # Replace the .*s in the regex with the proper separators
                        .replace('.*?', '\x12\x0c', 1)
                        .replace('.*?', '\x1a', 1)
                        .replace('.*?', '\x02\x08', 1)
                        .encode(),
                    ret[0]  # First line contains the spacecode, meetcode, and meeturl
                        .rstrip(b'\x01')  # Remove the end-of-message byte/character
                )[0]  # Use the one and only result
            )
        )
    )
    lookupcode = None
    if len(ret) >= 4:
        (lookupcode) = tuple(
            map(
                bytes.decode,
                tuple(
                    findall(
                        b"^(.+)\x00$",
                        ret[3]
                    )
                )
            )
        )

    gmeettoken = None
    try:
        gmeettoken = r.headers['x-goog-meeting-token']
    except KeyError:
        pass
    return (spacecode, meetcode, meeturl, gmeettoken, lookupcode)


if __name__ == '__main__':
    try:
        code = sys.argv[1].lower()
    except IndexError:
        print("No meeting identifier passed to script.")
        exit(128)
    if validate_meeting_code(code) is False:
        print("Unable to validate lookup code.")
        exit(6)
    else:
        ret = resolve_meeting_space(code)
        (spacecode, meetcode, meeturl, gmeettoken, lookupcode) = ret
        print(*filter(lambda item: item is not None, ret), sep='\n')
