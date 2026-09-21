"""Fyers daily login (their access token expires every day).

    python tools/fyers_login.py              -> prints the login URL
    python tools/fyers_login.py <auth_code>  -> exchanges the code, saves FYERS_ACCESS_TOKEN into .env

Steps: run it, open the URL, log in, and copy the `auth_code=...` value from the
address bar you are redirected to, then run it again with that code.
Needs FYERS_APP_ID, FYERS_SECRET_KEY, FYERS_REDIRECT_URI in .env.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import set_key  # noqa: E402
from fyers_apiv3 import fyersModel  # noqa: E402

from datalayer import config  # noqa: E402


def main():
    cred = config.require(["FYERS_APP_ID", "FYERS_SECRET_KEY", "FYERS_REDIRECT_URI"], "Fyers")
    session = fyersModel.SessionModel(
        client_id=cred["FYERS_APP_ID"],
        secret_key=cred["FYERS_SECRET_KEY"],
        redirect_uri=cred["FYERS_REDIRECT_URI"],
        response_type="code",
        grant_type="authorization_code",
    )

    if len(sys.argv) < 2:
        print("\nOpen this URL, log in and approve:\n")
        print(session.generate_authcode())
        print("\nYou will be redirected to a page whose address contains  auth_code=...")
        print("Copy that value, then run:  python tools/fyers_login.py <auth_code>")
        return

    session.set_token(sys.argv[1].strip())
    response = session.generate_token()
    if "access_token" not in response:
        sys.exit(f"Failed to get an access token: {response}")

    env_file = config.ENV_FILE
    env_file.touch(exist_ok=True)
    set_key(str(env_file), "FYERS_ACCESS_TOKEN", response["access_token"])
    print(f"Saved FYERS_ACCESS_TOKEN to {env_file}. Good until tonight.")


if __name__ == "__main__":
    main()
