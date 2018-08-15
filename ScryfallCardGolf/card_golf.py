import argparse
import datetime
import glob
import json
import os

import PIL.Image
import urllib.parse as urlparse
import shutil
import requests
from ScryfallCardGolf import *
from typing import Dict, Any, List, Mapping, Union
import TwitterAPI


def download_contents(url: str, download_type: str = 'json') -> Any:
    """
    Download contents from a URL
    :param url: URL to download
    :param download_type: Type of download (Default to JSON)
    :return: Contents
    """
    if download_type == 'json':
        request_response: Dict[str, Any] = requests.get(url=url).json()
    elif download_type == 'image':
        request_response: Any = requests.get(url, stream=True)
    else:
        request_response: Dict[str, Any] = dict()

    logging.debug('Downloaded URL {0}'.format(url))
    return request_response


def delete_temp_cards() -> None:
    """
    Delete the PNG images in the image folder
    """
    for card in glob.glob(TEMP_CARD_DIR + '/*.png'):
        logging.debug('Deleting file {0}'.format(card))
        os.remove(card)


def download_random_cards(number_of_cards: int) -> List[Dict[str, Any]]:
    """
    Download random cards from Scryfall for use in SF Card Golf
    :param number_of_cards: How many cards to play with
    :return: List of card objects requested
    """

    ret_val: List[Dict[str, Any]] = list()
    while number_of_cards > 0:
        request_api_json: Dict[str, Any] = download_contents(SCRYFALL_RANDOM_URL)
        ret_val.append(request_api_json)
        number_of_cards -= 1

    return ret_val


def resize_image(url_to_open: str) -> None:
    """
    Some of the image combinations created are too large for Twitter.
    This method will resize the image to allow for proper tweet size.
    This will resize in-place.
    :param url_to_open: Local URL to get image from
    """
    try:
        im = PIL.Image.open(url_to_open)
        im.thumbnail((1024, 512), PIL.Image.ANTIALIAS)
        im.save(url_to_open, 'PNG')
    except IOError:
        logging.error('Cannot create thumbnail for {0}'.format(url_to_open))


def send_tweet(message_to_tweet: str, url_to_media: str) -> int:
    """
    Send a tweet with an image.
    :param message_to_tweet: Message to send
    :param url_to_media: Image to upload
    :return: Tweet ID (-1 if it failed)
    """
    logging.debug('Tweet to send: {0}'.format(message_to_tweet))
    try:
        if url_to_media is not None:
            resize_image(url_to_media)
            photo = open(url_to_media, 'rb')
            status = twitter_api.request('statuses/update_with_media', {'status': message_to_tweet}, {'media[]': photo})
            logging.debug('Twitter Status Code: {0}'.format(status.status_code))

            response = TwitterAPI.TwitterResponse(status, False).json()
            logging.debug('Twitter Response Parsed: {0}'.format(response))
            return response['id_str']
    except UnicodeDecodeError:
        logging.error('Your message could not be encoded.  Perhaps it contains non-ASCII characters? ')

    return -1


def download_and_save_card_images(cards: List[Dict[str, Any]]) -> None:
    """
    Download and (temporarily) save card images for tweet processing
    :param cards: Cards to download and store
    :return: Nothing
    """
    for card in cards:
        card_image_url: str = card['image_uris']['png']
        request_image = download_contents(card_image_url, 'image')
        with open(TEMP_CARD_DIR + '{0}.png'.format(card['name'].replace('//', '_')), 'wb') as out_file:
            shutil.copyfileobj(request_image.raw, out_file)
        logging.debug('Saving image of card {0}'.format(card['name']))
        del request_image


def merge_card_images(cards: List[Dict[str, Any]]) -> str:
    """
    Taken from SO, but this method will merge all images in the
    images folder into one image. All prior images will be
    side-by-side
    :param cards: Cards to merge into one image
    :return: Resting URL of merged image
    """
    cards_to_merge: List[str] = glob.glob(TEMP_CARD_DIR + '*.png')

    images: Mapping[PIL.Image, List[str]] = map(PIL.Image.open, cards_to_merge)
    widths, heights = zip(*(i.size for i in images))

    total_width = sum(widths)
    max_height = max(heights)

    new_im = PIL.Image.new('RGB', (total_width, max_height))

    images = map(PIL.Image.open, cards_to_merge)
    x_offset = 0
    for im in images:
        new_im.paste(im, (x_offset, 0))
        x_offset += im.size[0]

    save_url: str = TEMP_CARD_DIR + '/{0}-{1}.png'.format(
        cards[0]['name'].replace('//', '_'),
        cards[1]['name'].replace('//', '_'))

    new_im.save(save_url)
    logging.debug('Saved merged image to {0}'.format(save_url))

    return save_url


def write_to_json_db(file_name: str, entry: Union[List[Dict[str, Any]], Dict[str, Any]]) -> None:
    """
    Write out a dictionary into the json database
    :param file_name: Database location
    :param entry: New dictionary entry to add
    :return: Nothing
    """

    feeds = list()
    if isinstance(entry, dict):
        feeds = dict()

    if not os.path.isfile(file_name):
        if isinstance(feeds, dict):
            feeds[str(time.strftime('%Y-%m-%d_%H:%M:%S'))] = entry
        else:
            feeds = entry

        with open(file_name, mode='w') as f:
            f.write(json.dumps(feeds, indent=4, sort_keys=True))
    else:
        with open(file_name) as json_feed:
            feeds = json.load(json_feed)

        if isinstance(feeds, dict):
            feeds[str(time.strftime('%Y-%m-%d_%H:%M:%S'))] = entry
        else:
            feeds += entry

        with open(file_name, mode='w') as f:
            f.write(json.dumps(feeds, indent=4, sort_keys=True))


def load_json_db(file_name: str) -> Dict[str, Any]:
    """
    Load the database and return the contents
    :param file_name: Location of database
    :return: Database contents
    """
    if not os.path.isfile(file_name):
        return dict()

    with open(file_name) as json_feed:
        return json.load(json_feed)


def is_active_contest_already() -> bool:
    """
    Determine if there is a current competition live.
    If the contest is finished, gather the results (separate function)
    :return: Active contest status
    """
    # See if a current contest is active
    json_db: Dict[str, Any] = load_json_db(TWEET_DATABASE)
    try:
        max_key: str = max(json_db.keys())
    except ValueError:
        logging.warning("Database was empty, continuing")
        return False

    current_contest_start_date: datetime.datetime = datetime.datetime.strptime(max_key, '%Y-%m-%d_%H:%M:%S')
    current_contest_end_date: datetime.datetime = current_contest_start_date + datetime.timedelta(days=1)

    if current_contest_end_date > datetime.datetime.now():
        logging.warning('Current contest from {0} still active'.format(max_key))
        return True

    write_results(get_results())
    return False


def test_query(user_name: str, scryfall_url: str) -> str:
    """
    Load up the Scryfall URL tweeted by the user and see if it
    matches the competition requirements (i.e. is it exclusively
    the two cards we are looking for)
    :param user_name: Twitter username
    :param scryfall_url: Scryfall URL they tweeted
    :return: Winning query ('' if failed)
    """
    try:
        query: str = urlparse.parse_qs(urlparse.urlparse(scryfall_url).query)['q'][0]

        scryfall_api_url = 'https://api.scryfall.com/cards/search?q={0}'.format(query)
        response: Dict[str, Any] = download_contents(scryfall_api_url)

        if response['total_cards'] != 2:
            logging.info('{0} result has wrong number of cards: {1}'.format(user_name, response['total_cards']))

        json_db: Dict[str, Any] = load_json_db(TWEET_DATABASE)
        max_key: str = max(json_db.keys())
        valid_cards: List[str] = [json_db[max_key]['cards'][0]['name'], json_db[max_key]['cards'][1]['name']]
        for card in response['data']:
            if card['name'] not in valid_cards:
                logging.info('{0} result has wrong card: {1}'.format(user_name, card['name']))
                return ''

        if 'or' in query.lower():
            logging.info("{0} was correct, but they used 'OR': {1}".format(user_name, query))
            return ''

        # Correct response!
        logging.info('{0} was correct! [ {1} ] ({2})'.format(user_name, query, len(query)))
        return query
    except KeyError:
        logging.info('{0} submitted a bad Scryfall URL: {1}'.format(user_name, scryfall_url))
        return ''


def get_results() -> List[Dict[str, Any]]:
    """
    Get the results from the competition and print it out
    :return: Winner's name and their query
    """
    valid_entries: List[Dict[str, Any]] = list()

    logging.info('CONTEST OVER -- RESULTS')
    r = TwitterAPI.TwitterPager(twitter_api, 'search/tweets', {'q': '#ScryfallCardGolf', 'count': 100})
    for item in r.get_iterator():
        if 'text' in item:
            logging.debug(item['user']['screen_name'] + ': ' + item['text'])
            for url in item['entities']['urls']:
                test_url = url['expanded_url']
                if 'scryfall.com' in test_url:
                    logging.debug('{0} submitted solution: {1}'.format(item['user']['screen_name'], test_url))
                    test_query_results = test_query(item['user']['screen_name'], test_url)
                    if len(test_query_results) > 0:
                        valid_entries.append({
                            'name': item['user']['screen_name'],
                            'length': len(test_query_results),
                            'query': test_query_results
                        })

            # The submitted can't enter, so this means it's the end of the tweet train
            if item['user']['screen_name'] == TWEETER_ACCOUNT:
                break
        elif 'message' in item and item['code'] == 88:
            logging.warning('SUSPEND, RATE LIMIT EXCEEDED: %s\n' % item['message'])
            break

    return valid_entries


def write_results(results: List[Dict[str, Any]]) -> None:
    """
    Take a list of results and put it to the winners file for that contest
    :param results: List of winners
    """
    file_key: str = max(load_json_db(TWEET_DATABASE).keys())
    write_to_json_db('../winners_{0}.json'.format(file_key), results)


def main(force_new: bool = False) -> None:
    # If contest is over, print results and continue. Otherwise exit
    if not force_new and is_active_contest_already():
        exit(0)

    # Clear out the cards directory
    delete_temp_cards()

    # Get 2 random cards
    cards: List[Dict[str, Any]] = download_random_cards(2)
    card1 = '{0}: {1}'.format(cards[0]['name'], cards[0]['scryfall_uri'])
    card2 = '{0}: {1}'.format(cards[1]['name'], cards[1]['scryfall_uri'])

    for card in cards:
        logging.debug('Card to merge: {0}'.format(card['name']))

    # Save the images
    download_and_save_card_images(cards)

    # Merge the images
    tweet_image_url: str = merge_card_images(cards)

    message = 'Can you make both of these cards show up in a Scryfall search without using \'or\'?\n• {0}\n• ' \
              '{1}\nRespond with a Scryfall URL and the #ScryfallCardGolf hash tag in the next 24 hours to enter!' \
              .format(card1, card2)

    # Send the tweet
    tweet_id: int = send_tweet(message, tweet_image_url)

    json_entry: Dict[str, Any] = {'tweet_id': tweet_id, 'cards': [
            {'name': cards[0]['name'], 'url': cards[0]['scryfall_uri']},
            {'name': cards[1]['name'], 'url': cards[1]['scryfall_uri']}
        ]}

    write_to_json_db(TWEET_DATABASE, json_entry)


if __name__ == '__main__':
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description='Handle Scryfall Card Golf')
    parser.add_argument('--results', action='store_true', help='get latest contest results')
    parser.add_argument('--force-new', action='store_true', help='force start next contest')

    args = parser.parse_args()

    if args.results:
        correct_users = get_results()
        write_results(correct_users)

    if args.force_new:
        main(True)
    else:
        main()
