from telegram.ext import (Updater,
                          CommandHandler, ConversationHandler, RegexHandler, MessageHandler,
                          Filters)

from telegram import (KeyboardButton, ReplyKeyboardMarkup,
                      InlineKeyboardMarkup, InlineKeyboardButton,
                      ReplyKeyboardRemove)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import logging
from random import randint, choice, shuffle
import json
from sys import exit
from string import ascii_letters, digits

from app.models import Joke, User
from app.exceptions import *

logger = logging.getLogger(__name__)

USERNAME_RECEIVED = 0
JOKE_RECEIVED = 0


class HahOrNahBot:
    def __init__(self, token, database_url):
        self.JOKE_LENGTH_MIN = 10
        self.JOKE_LENGTH_MAX = 1000
        self.USERNAME_LENGTH_MIN = 5
        self.USERNAME_LENGTH_MAX = 20
        self.USERNAME_ALLOWED_CHARACTERS = set(ascii_letters + digits + '-_')

        self.RESPONSES_FILE = 'bot_responses/bot_responses.json'
        self.responses = self.private_get_responses(self.RESPONSES_FILE)

        self.token = token
        self.database_url = database_url
        self.updater = Updater(token=token)
        self.dispatcher = self.updater.dispatcher

        start_handler = CommandHandler('start', self.menu, pass_user_data=True)
        help_handler = CommandHandler('help', self.help)
        cancel_handler = CommandHandler('cancel', self.cancel)

        random_joke_handler = CommandHandler('random_joke', self.display_random_joke, pass_user_data=True)
        random_favorite_joke_handler = CommandHandler('random_favorite_joke', self.display_random_favorite_joke,
                                                      pass_user_data=True)
        best_joke_handler = CommandHandler('best_joke', self.display_best_joke, pass_user_data=True)
        vote_handler = RegexHandler('^(/hah|/nah)$', self.vote_for_joke, pass_user_data=True)
        profile_handler = CommandHandler('profile', self.profile, pass_user_data=True)
        top10_handler = CommandHandler('top10', self.top10, pass_user_data=True)

        # Whenever the method `self.private_get_user` raises an exception, keyboard with two options is displayed.
        # /whatever string is stored in 'user_new_keyboard_button' in bot_responses.json and /cancel
        # This ConversationHandler is entered when the first button is clicked.
        new_user_keyboard_string = self.private_get_one_response('user_new_keyboard_button')
        new_user_handler = ConversationHandler(
            entry_points=[RegexHandler("{}".format(new_user_keyboard_string), self.new_user_prompt)],
            states={
                USERNAME_RECEIVED: [MessageHandler(Filters.text,
                                                   self.new_user_received_username,
                                                   pass_user_data=True)
                                    ],
            },
            fallbacks=[cancel_handler]
        )

        new_joke_handler = ConversationHandler(
            entry_points=[RegexHandler("/add_joke", self.new_joke_prompt)],
            states={
                JOKE_RECEIVED: [MessageHandler(Filters.text,
                                               self.new_joke_received,
                                               pass_user_data=True)
                                ]
            },
            fallbacks=[cancel_handler]
        )

        menu_handler = RegexHandler('.*', self.menu, pass_user_data=True) # any message
        handlers = [start_handler,
                    help_handler,
                    new_user_handler,
                    new_joke_handler,
                    cancel_handler,

                    random_joke_handler,
                    random_favorite_joke_handler,
                    best_joke_handler,
                    vote_handler,

                    profile_handler,
                    top10_handler,

                    menu_handler]

        for handler in handlers:
            self.dispatcher.add_handler(handler)

        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine)
        self.session = Session()

    # PRIVATE METHODS

    def private_get_responses(self, responses_file):
        """
        Get bot responses defined in self.RESPONSES_FILE

        Returns:
            dict
        """
        try:
            with open(responses_file, 'r') as fp:
                responses = json.load(fp)
            return responses

        except FileNotFoundError:
            logger.info('Responses file {} not found. Exiting'.format(responses_file))
            exit()

    def private_get_random_response(self, state):
        """
        Return random response from config_file

        Returns:
            string
        """
        try:
            assert state in self.responses.keys()  # should be called only with states defined in self.RESPONSES_FILE
        except AssertionError:
            logger.info('No response found for ' + state)
            logger.info(self.responses.keys())
            exit()

        response = choice(self.responses[state])
        return response

    def private_get_one_response(self, state):
        """
        Return response from config_file when there is only one possible

        Returns:
            string

        """
        try:
            assert state in self.responses.keys()  # should be called only with states defined in self.RESPONSES_FILE
        except AssertionError:
            logger.info('No response found for ' + state)
            logger.info(self.responses.keys())
            exit()

        response = self.responses[state][0]
        return response

    def private_get_user(self, message, user_data):
        """
        Get user by id if the user is in database, raise exception if user is not found.

        Returns:
            instance of User class if user exists

        Raises:
            UserDoesNotExist
        """

        # Check user in cache
        try:
            user = user_data['current_user']
            return user
        except KeyError:
            pass

        # Check if user is in database
        user_id = message.chat.id
        user = self.session.query(User).filter(User.id == user_id).first()
        if user is None:
            raise UserDoesNotExist

        else:
            user_data['current_user'] = user
            return user

    def private_add_user(self, user_id, username):
        """
        Add new user to database

        Returns:
            User object

        Raises:
            InvalidCharacters
            TooShort
            TooLong
        """

        only_allowed_chars = set(username) <= self.USERNAME_ALLOWED_CHARACTERS
        if not only_allowed_chars:
            raise InvalidCharacters

        if len(username) < self.USERNAME_LENGTH_MIN:
            raise TooShort

        if self.USERNAME_LENGTH_MAX < len(username):
            raise TooLong

        user = self.session.query(User).filter(User.id == user_id).first()
        assert user is None  # should be called only for new users

        user = User(id=user_id, username=username, score=0)
        self.session.add(user)
        self.session.commit()
        return user

    def private_add_joke(self, joke_body, author):
        """
        Add new joke to database

        Arguments:
            joke_body: str
            author: User

        Returns:
            None

        Raises:
            InvalidCharacters
            TooShort
            TooLong
        """
        if len(joke_body) < self.JOKE_LENGTH_MIN:
            raise TooShort

        if self.JOKE_LENGTH_MAX < len(joke_body):
            raise TooLong

        # Calculate id by adding one to last joke's id
        all_jokes = self.session.query(Joke).order_by(Joke.id).all()
        try:
            last_joke = all_jokes[-1]
            last_joke_id = last_joke.get_id()
            joke_id = last_joke_id + 1
        except IndexError:  # no jokes in database
            joke_id = 0

        new_joke = Joke(id=joke_id, body=joke_body, vote_count=0, author=author)
        self.session.add(new_joke)
        self.session.commit()
        return

    def private_get_message(self, update):
        """
        Depending on the type of response, message object can be located in update.message or update.message.callback_query.

        Returns:
            Message
        """
        try:
            message = update.callback_query.message
        except AttributeError:
            message = update.message  # this line returns None if a callback is used so it wouldn't work vice-versa

        return message

    # SHOW KEYBOARDS METHODS

    def display_new_user_keyboard(self, bot, update):
        """
        Display keyboard prompt to register new user.

        text located in `user_new_keyboard_button` in responses file | /cancel
        """
        keyboard_buttons = [[KeyboardButton(self.private_get_one_response('user_new_keyboard_button'))],
                            [KeyboardButton('/cancel')]]
        bot.send_message(chat_id=update.message.chat_id,
                         text=self.private_get_random_response('user_not_registered'),
                         reply_markup=ReplyKeyboardMarkup(keyboard_buttons, one_time_keyboard=True))
        return

    def display_new_joke_keyboard(self, bot, update):
        """
        Display keyboard prompt to add new joke

        text located in `joke_new_keyboard_button` in responses file | /cancel
        """
        message = update.message
        keyboard_buttons = [[KeyboardButton(self.private_get_one_response('joke_new_keyboard_button'))],
                            [KeyboardButton('/cancel')]]
        bot.send_message(chat_id=message.chat_id,
                         text=self.private_get_random_response('joke_new_ask'),
                         reply_markup=ReplyKeyboardMarkup(keyboard_buttons, one_time_keyboard=True))
        return

    def display_menu_keyboard(self, bot, update):
        """
        Display menu

        """
        menu_options = [
            [KeyboardButton('/random_joke')],
            [KeyboardButton('/random_favorite_joke')],
            [KeyboardButton('/best_joke')],
            [KeyboardButton('/add_joke')],
            [KeyboardButton('/profile')],
            [KeyboardButton('/top10')],
            [KeyboardButton('/help')],
        ]

        keyboard = ReplyKeyboardMarkup(menu_options)
        bot.send_message(chat_id=update.message.chat_id,
                         text=self.private_get_random_response('menu'),
                         reply_markup=keyboard)
        return

    def display_vote_keyboard(self, bot, update):
        """
        Display vote options.

        /hah | /nah
        """
        vote_options = [
            [KeyboardButton('/hah')],
            [KeyboardButton('/nah')],
        ]

        vote_options_keyboard = ReplyKeyboardMarkup(vote_options, one_time_keyboard=True)
        bot.send_message(chat_id=update.message.chat.id,
                         text=self.private_get_random_response('hah_or_nah'),
                         reply_markup=vote_options_keyboard)
        return

    def remove_keyboard(self, update, bot, text):
        """
        Remove any keyboard

        Arguments:
            text: string to be displayed
        """
        remove_keyboard = ReplyKeyboardRemove()
        bot.send_message(chat_id=update.message.chat.id,
                         text=text,
                         reply_markup=remove_keyboard)

        return

    # COMMAND METHODS
    def help(self, bot, update):
        message = update.message
        help_message = '''
        *Commands*
        /help - Display this message
        /menu - Display commands keyboard
        /random\_joke - Display random joke
        /random\_favorite\_joke - Display random joke from favorites
        /best\_joke - Display joke with the most votes that you didn't see yet.
        /add\_joke - Proceed to add a joke
        /profile - Show user profile
        /top\_10 - Show top 10 users by score
        /cancel - Cancel current action (adding joke/registering user)
        '''
        message.reply_markdown(help_message)
        return

    def cancel(self, bot, update):
        message = update.message
        message.reply_text(self.private_get_random_response('cancel'))
        self.display_menu_keyboard(bot, update)
        return ConversationHandler.END

    def menu(self, bot, update, user_data):
        message = update.message
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        self.display_menu_keyboard(bot, update)

    def new_user_prompt(self, bot, update):
        message = update.message
        message.reply_text(self.private_get_random_response('user_new_prompt'))
        return USERNAME_RECEIVED

    def new_user_received_username(self, bot, update, user_data):
        message = update.message
        username = message.text
        user_id = message.chat.id

        try:
            user = self.private_add_user(user_id, username)
            user_data['user'] = user

            message.reply_text(self.private_get_random_response('user_register_success'))
            self.display_menu_keyboard(bot, update)

        except InvalidCharacters:
            error_message = self.private_get_random_response('username_invalid_characters')
            message.reply_text(error_message)
        except TooShort:
            error_message = self.private_get_random_response('username_too_short')
            message.reply_text(error_message)
        except TooLong:
            error_message = self.private_get_random_response('username_too_long')
            message.reply_text(error_message)
        finally:
            return

    def new_joke_prompt(self, bot, update):
        message = update.message
        remove_keyboard = ReplyKeyboardRemove()
        reply_message = self.private_get_random_response('joke_new_prompt')
        bot.send_message(chat_id=message.chat.id,
                         text=reply_message,
                         reply_markup=remove_keyboard)

        return JOKE_RECEIVED

    def new_joke_received(self, bot, update, user_data):
        # Check if user is registered
        message = update.message
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        joke_body = message.text
        try:
            self.private_add_joke(joke_body, user)
            message.reply_text(self.private_get_random_response('joke_submitted'))
            self.display_menu_keyboard(bot, update)
        except TooShort:
            error_message = self.private_get_random_response('joke_too_short')
            message.reply_text(error_message)
        except TooLong:
            error_message = self.private_get_random_response('joke_too_long')
            message.reply_text(error_message)
        finally:
            return

    def display_random_joke(self, bot, update, user_data):
        """
        Show random joke
        """
        message = update.message

        # Check if user is registered
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        # Check if database is not empty
        all_jokes = self.session.query(Joke).all()
        try:
            random_joke_index = randint(0, len(all_jokes) - 1)
        except ValueError:
            message.reply_text(self.private_get_random_response('no_new_jokes'))
            return

        shuffle(all_jokes)

        for random_joke in all_jokes:
            voted_already = random_joke in user.jokes_voted_for
            user_is_author = random_joke in user.jokes_submitted
            if not voted_already and not user_is_author:
                # Remember last joke displayn - used in self.vote_for_joke to vote for right joke
                user_data['last_joke'] = random_joke
                # Display joke
                message.reply_text(random_joke.get_body())
                self.display_vote_keyboard(bot, update)
                return

        message.reply_text(self.private_get_random_response('no_new_jokes'))
        return

    def display_random_favorite_joke(self, bot, update, user_data):
        """
        Show random joke from jokes user voted for.
        """

        # Check if user is registered
        message = update.message
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        # Check if there are any jokes marked as favorite
        all_favorite_jokes = user.get_jokes_voted_positive()
        try:
            random_joke = choice(all_favorite_jokes)
        except IndexError:
            message.reply_text(self.private_get_random_response('joke_no_favorite'))
            return

        # Display joke
        message.reply_text(random_joke.get_body())
        return

    def display_best_joke(self, bot, update, user_data):
        """
        Show joke with the highest number of votes the user hasn't seen yet.
        """
        # Check if user is registered
        message = update.message
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        jokes_by_score = self.session.query(Joke).order_by(Joke.vote_count).all()
        for joke in jokes_by_score:
            voted_already = joke in user.jokes_voted_for
            user_is_author = joke in user.jokes_submitted
            if not voted_already and not user_is_author:
                message.reply_text(joke.get_body())
                return



    def vote_for_joke(self, bot, update, user_data):
        """
        Register user's vote for joke.
        """
        message = update.message
        # Check if user is registered
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        # Check if is called after displaying a joke
        try:
            joke = user_data['last_joke']
        except KeyError:
            message.reply_text(self.private_get_random_response('joke_no_current'))
            return

        try:
            if 'hah' in message.text:
                user.vote_for_joke(joke, positive=True)
            else:
                user.vote_for_joke(joke, positive=False)

            self.session.add(user, joke)
            self.session.commit()

        except InvalidVote as e:
            logger.error(e)

        finally:
            self.remove_keyboard(update, bot, self.private_get_random_response('after_vote'))
            del user_data['last_joke']
            return


    def profile(self, bot, update, user_data):
        """
        Show information about user.
        """
        message = update.message
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        all_users = self.session.query(User).order_by(User.score).all()
        user_rank = all_users.index(user) + 1
        jokes_submitted_count = len(user.get_jokes_submitted())
        average_score = user.get_average_score()

        width = 10
        username_line = '*{}*'.format(user.get_username())
        rank_line = 'rank: {rank}. ({score} points)'.format(rank=user_rank, width=width, score=user.get_score())
        jokes_submitted_line = "jokes submitted: {jokes_count} ({average_score} points/joke)".format(
            jokes_count=jokes_submitted_count, average_score=average_score)

        user_info = '\n'.join([username_line, rank_line, jokes_submitted_line])
        message.reply_markdown(user_info)
        return

    def top10(self, bot, update, user_data):
        """
        Show top 10 users by score.
        """
        message = update.message
        try:
            user = self.private_get_user(message, user_data)
        except UserDoesNotExist:
            self.display_new_user_keyboard(bot, update)
            return

        all_users = self.session.query(User).order_by(User.score).all()
        top_10_users = all_users[:10]
        reply_message = ''

        for index, user in enumerate(top_10_users):
            rank = index + 1
            reply_message += '{rank}. {username} - score: {score}\n'.format(rank=rank, username=user.get_username(),
                                                                            score=user.get_score())

        message.reply_text(reply_message)
        return

    def start_webhook(self, url, port):
        self.updater.start_webhook(listen="0.0.0.0",
                                   port=port,
                                   url_path=self.token)
        self.updater.bot.set_webhook(url + self.token)
        self.updater.idle()
        return

    def start_local(self):
        self.updater.start_polling()
        self.updater.idle()