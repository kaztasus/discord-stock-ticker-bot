import logging
from os import getenv
from sqlite3 import connect
from json import dumps
import base64

import docker
from requests import get, patch, post, auth
from discord_webhook import DiscordWebhook, DiscordEmbed


COINGECKO_URL = 'https://api.coingecko.com/api/v3/'
YAHOO_URL = 'https://query1.finance.yahoo.com/v10/finance/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'accept': 'application/json'
}


def log(message: str) -> None:
    '''
    Send message to discord
    '''

    logging.info(message)

    discord_msg = DiscordWebhook(
        url=getenv('DISCORD_ADMIN_WEBHOOK')
    )

    discord_msg.add_embed(
        DiscordEmbed(
            title='API Log',
            description=message,
            color='ffb300'
        )
    )

    return discord_msg.execute().status_code


def change_bot_username(token: str, username: str):
    '''
    Change the username of the bot
    '''

    resp = patch(
        'https://discord.com/api/users/@me',
        headers={
            'Authorization': f'Bot {token}'
        },
        json={
            'username': username.upper()
        }
    )

    try:
        resp.raise_for_status()
    except:
        return False
    
    return resp.json().get('username')


def change_bot_photo(token: str, base64_photo: str):
    '''
    Change the photo of the bot
    '''

    resp = patch(
        'https://discord.com/api/users/@me',
        headers={
            'Authorization': f'Bot {token}',
            'Content-Type': 'application/json'
        },
        json={
            'avatar': base64_photo
        }
    )

    try:
        resp.raise_for_status()
    except:
        return False

    if not resp.json().get('avatar', False):
        return False

    return True


def create_bot(ticker: str, name: str, client_id: str, token: str, is_crypto: bool) -> bool:
    '''
    Create a new bot instance
    Returns a container instance of the bot
    '''

    data = {
        "ticker": ticker,
        "name": name,
        "crypto": is_crypto,
        "frequency": 90,
        "discord_bot_token": token
    }

    resp = post(
        getenv("URL") + '/ticker',
        auth=auth.HTTPBasicAuth(getenv('AUTH_USER'), getenv('AUTH_PASS')),
        data=dumps(data)
    )

    if resp.status_code == 200:
        change_bot_username(token, name)
        return True
    else:
        logging.error(str(resp.status_code) + " " + resp.text)
        log(resp.text)
        return False


def crypto_search(key: str) -> list:
    '''
    Search for a crypto
    Returns a list of possible ids
    '''

    resp = get(
        COINGECKO_URL + 'coins/list',
        headers=HEADERS
    )

    try:
        resp.raise_for_status()
        data = resp.json()
    except:
        logging.error('Bad response from CG')
        return ()

    return [x['id'] for x in data if key in x['id'] or key in x['symbol'] or key in x['name']]


def crypto_validate(id: str) -> tuple:
    '''
    Validate a crypto ticker
    Returns crypto id and name
    '''

    resp = get(
        COINGECKO_URL + f'coins/{id}',
        headers=HEADERS
    )

    try:
        resp.raise_for_status()
        data = resp.json()
    except:
        logging.error('Bad response from CG')
        return ()

    return (data['id'], data['symbol'])


def stock_validate(id: str) -> tuple:
    '''
    Validate a stock ticker
    Returns stock id and name
    '''

    resp = get(
        YAHOO_URL + f'quoteSummary/{id}?modules=price',
        headers=HEADERS
    )

    try:
        resp.raise_for_status()
        data = resp.json()
    except:
        logging.error('Bad response from yahoo')
        return None
    
    if data['quoteSummary']['error']:
        logging.error(f'not a valid ticker: {id}')
        return None

    if 'currencySymbol' not in data['quoteSummary']['result'][0]['price']:
        logging.error(f'not a valid ticker: {id}')
        return None

    symbol = data['quoteSummary']['result'][0]['price']['symbol'].lower()
    return (symbol, symbol)


def check_existing_bot(ticker: str):
    '''
    Check if a bot already exists for the given ticker
    Returns the client id of the existing bot
    '''

    db_client = connect(getenv('DB_PATH') + getenv('PUBLIC_DB'))

    # Get an unused bot
    get_cur = db_client.cursor()
    get_cur.execute(
        'SELECT client_id, token FROM newbots WHERE ticker = ?',
        (ticker,)
    )

    try:
        existing_bot = get_cur.fetchone()
        db_client.close()
    except TypeError:
        logging.info(f'No bot exists for {ticker}')
        return None
    
    db_client.close()

    if not existing_bot:
        logging.info(existing_bot)
        logging.info(f'We already have a bot for {ticker}')
        return None

    return (existing_bot[0], existing_bot[1])


def change_ticker_photo(ticker: str, url: str):
    '''
    Change profile photo for bot
    Download given image, format for discord api
    '''

    # If we already have a bot, return the client id
    client_id = check_existing_bot(ticker)
    if not client_id:
        return False

    # Download photo
    photo_data = get(url).content

    # base64 encode file
    photo_encoded = f'data:image/{url.split(".")[-1]};base64,' + base64.b64encode(photo_data).decode('ascii')
    print(f"photo encoded: {photo_encoded}")

    return change_bot_photo(client_id[1], photo_encoded)


def get_new_bot(ticker: str, typ: str) -> tuple:
    '''
    Get a new bot from the DB
    Returns the new bots id and token, or just id if bot already existed
    '''

    # If we already have a bot, return the client id
    client_id = check_existing_bot(ticker)
    if client_id:
        return (client_id[0], None)

    db_client = connect(getenv('DB_PATH') + getenv('PUBLIC_DB'))

    # Get an unused bot
    get_cur = db_client.cursor()
    get_cur.execute(
        'SELECT client_id, token FROM newbots WHERE ticker IS NULL'
    )

    try:
        new_bot = get_cur.fetchone()
    except TypeError:
        log('Unable to get new bot from db')
        return ()
   
    if not new_bot:
        log('Unable to get new bot from db')
        return ()

    # Before we use the new bot, claim it
    claim_cur = db_client.cursor()
    claim_cur.execute(
        'UPDATE newbots SET ticker = ?, type = ? WHERE client_id = ?',
        (ticker.lower(), typ, new_bot[0])
    )

    if claim_cur.rowcount == 0:
        log('Unable to claim new bot in db')
        return ()

    db_client.commit()

    return new_bot


def crypto(id: str):

    # Validate crypto id with cg
    crypto_details = crypto_validate(id.lower())

    if not crypto_details:
        possible_id = check_existing_bot(id.lower())
        if possible_id:
            return {
                'client_id': possible_id[0],
                'existing': True
            }
        log(f'unable to validate coin id: {id}')
        return {'error': f'unable to validate coin id: {id}'}

    # Query db for client_id and token
    bot_details = get_new_bot(crypto_details[0], 'crypto')

    # No new bots avalible
    if not bot_details:
        log('no more new bots avalible')
        return {'error': 'there are no more unclaimed bots. come back tomorrow and there might be more available'}
    
    # Bot already existed
    if not bot_details[1]:
        log(f'existing bot requested: {id}')
        return {
            'client_id': bot_details[0],
            'existing': True
        }

    # Create new bot instance
    log(f'attempting to create new bot: {id}')
    success = create_bot(
        crypto_details[1],
        crypto_details[0],
        bot_details[0],
        bot_details[1],
        True
    )

    if success:
        log(
            f'crypto: `[{crypto_details[1]}](https://discord.com/api/oauth2/authorize?client_id={bot_details[0]}&permissions=0&scope=bot)`'
        )
        return {'client_id': bot_details[0]}
    else:
        return {'error': 'having trouble starting new bot'}


def stock(id: str):

    # Validate stock id with yahoo
    stock_details = stock_validate(id.lower())

    if not stock_details:
        possible_id = check_existing_bot(id.lower())
        if possible_id:
            return {
                'client_id': possible_id,
                'existing': True
            }
        log(f'unable to validate stock id: {id}')
        return {'error': f'unable to validate stock id: {id}'}

    # Query db for client_id and token
    bot_details = get_new_bot(stock_details[0], 'stock')

    # No new bots avalible
    if not bot_details:
        log('no more new bots avalible')
        return {'error': 'no more new bots avalible'}
    
    # Bot already existed
    if not bot_details[1]:
        log(f'existing bot requested: {id}')
        return {
            'client_id': bot_details[0],
            'existing': True
        }

    # Create new bot instance
    log(f'attempting to create new bot: {id}')
    success = create_bot(
        stock_details[1],
        stock_details[0],
        bot_details[0],
        bot_details[1],
        False
    )

    if success:
        log(
            f'stock: `[{stock_details[1]}](https://discord.com/api/oauth2/authorize?client_id={bot_details[0]}&permissions=0&scope=bot)`'
        )
        return {'client_id': bot_details[0]}
    else:
        return {'error': 'having trouble starting new bot'}


def add_bot(client_id: str, token: str) -> bool:
    '''
    Add a new bot to the db
    '''

    # Verify the token is valid
    if not change_bot_username(token, 'new ticker bot'):
        log(f'unable to change the name for {client_id}')
        return False

    db_client = connect(getenv('DB_PATH') + getenv('PUBLIC_DB'))

    # Get an unused bot
    new_cur = db_client.cursor()
    new_cur.execute(
        'INSERT INTO newbots VALUES (?, ?, NULL, NULL)',
        (client_id, token)
    )

    if new_cur.rowcount == 0:
        log('Unable to add new bot to db')
        return False

    db_client.commit()

    return True


def add_private_bot(db: str, client_id: str, token: str, ticker: str, typ: str) -> bool:
    '''
    Add a new private bot to the db
    '''

    # Verify the token is valid
    if not change_bot_username(token, ticker):
        log(f'unable to change the name for {client_id}')
        return False

    db_client = connect(getenv('DB_PATH') + db + '.db')

    # Get an unused bot
    new_cur = db_client.cursor()
    new_cur.execute(
        'INSERT INTO newbots VALUES (?, ?, ?, ?)',
        (client_id, token, ticker, typ)
    )

    if new_cur.rowcount == 0:
        log('Unable to add new bot to db')
        return False

    db_client.commit()

    return True

