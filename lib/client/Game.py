from .Connection import Connection
from .Fleet import Fleet
from .ui import UIData, TitleScreen, BattleScreen
from CustomExceptions import *
import curses
import atexit


def run(direct_p2p_data):
    """
    wraps the client into a curses environment which does all the basic
    curses initialisations and deinitialisations automatically.
    :param direct_p2p_data: contains a dictionary with direct p2p client
    connection data, if this mode was chosen via command line. otherwise, it
    contains None
    """
    curses.wrapper(_run_game, direct_p2p_data)


def _run_game(stdscr, direct_p2p_data):
    """
    top level game logic.
    :param stdscr: curses default window, passed by wrapper method
    :param direct_p2p_data: contains a dictionary with direct p2p client
    connection data, if this mode was chosen via command line. otherwise, it
    contains None
    """
    UIData.init_colors()
    TitleScreen.init()

    connection = Connection()
    atexit.register(connection.ensure_closing)

    try:
        if direct_p2p_data:
            _establish_direct_p2p_connection(connection, direct_p2p_data)
            player_name = direct_p2p_data['player name']
            is_host = direct_p2p_data['as host']
        else:
            player_name = _connect_to_server(connection)
            is_host = _establish_game_connection(connection, player_name)

        opponent_name = connection.exchange_names(player_name)
        BattleScreen.introduce_opponent(opponent_name)
        BattleScreen.show_ship_placement_keys()

        while True:  # runs until Exception is raised
            _run_battle(connection, opponent_name, is_host)

    except ServerShutdown:      # server got killed
        TitleScreen.shutdown_info()
    except (ProgramExit, KeyboardInterrupt):   # local client got killed
        if connection.established: connection.inform_exit()
    except OpponentLeft:        # remote player left
        BattleScreen.handle_exit('%s has left the game!' % opponent_name)


def _establish_direct_p2p_connection(connection, direct_p2p_data):
    """
    establishes a direct p2p connection to another client, circumventing the
    server. this client can either take the role of a host or of a client
    connecting directly to a host.
    :param connection: the connection object, not connected yet
    :param direct_p2p_data: dictionary holding direct p2p client connection data
    """
    if direct_p2p_data['as host']:
        TitleScreen.wait_message()
        if connection.wait_for_connection():
            TitleScreen.uninit()
            BattleScreen.init(direct_p2p_data['player name'])
        else:
            TitleScreen.inform_direct_p2p_failure(as_host=True)
            raise ProgramExit
    else:
        if connection.connect_to_host(direct_p2p_data['host ip']):
            TitleScreen.uninit()
            BattleScreen.init(direct_p2p_data['player name'])
        else:
            TitleScreen.inform_direct_p2p_failure(as_host=False)
            raise ProgramExit


def _connect_to_server(connection):
    """
    establishes a connection to the server, requiring user input from the
    title screen.
    :param connection: the connection object, not connected yet
    :return: the name of the player
    """
    while True:
        player_name, server_ip = TitleScreen.server_logon()
        if connection.connect_to_server(server_ip):
            return player_name.decode('utf-8') # curses returns bytestring
        else:
            if not TitleScreen.ask_server_connection_retry():
                raise ProgramExit


def _establish_game_connection(connection, player_name):
    """
    gives the player the opportunity to either join a hosted game or to host a
    game himself.
    :param connection: the connection object
    :param player_name: the name of the player
    :return: True if the player decided to be host, False otherwise
    """
    while True:
        host_ip = TitleScreen.select_host(connection.available_hosts)

        if(host_ip):
            if connection.connect_to_host(host_ip):
                TitleScreen.uninit()
                BattleScreen.init(player_name)
                return False

        else:  # host ip is None: player wants to host a game
            if connection.register_as_host(player_name):
                TitleScreen.wait_message()
                if connection.wait_for_connection():
                    TitleScreen.uninit()
                    BattleScreen.init(player_name)
                    return True

        TitleScreen.inform_game_launch_failure(host_ip == None)



def _run_battle(connection, opponent_name, player_starts):
    """
    runs one battle between two players. includes initial ship placements.
    only returns once an exception is thrown.
    :param connection: the connection to the opponent's client
    :param opponent_name: the name of the opponent
    :param player_starts: boolean indicating whether player is first to shoot
    """
    fleet = _handle_ship_placements(connection, opponent_name, player_starts)

    try:
        if player_starts:
            _player_shot(connection, opponent_name, fleet)
        while True: # can only exit through exception throw
            _opponent_shot(connection, opponent_name, fleet)
            _player_shot(connection, opponent_name, fleet)
    except PlayAgain:
        BattleScreen.reset_battle()
        return


def _handle_ship_placements(connection, opponent_name, player_starts):
    """
    lets player place his ships and waits for the opponent to either place
    his ships or exit.
    :param connection: the connection to the opponent's client
    :param opponent_name: the name of the opponent
    :param player_starts: boolean indicating whether player is first to shoot
    :return: the fleet placed by the player
    """
    ships = (5, 4, 4, 3, 3, 3, 2, 2, 2, 2)
    ship_placements = BattleScreen.player_ship_placements(ships)
    BattleScreen.show_battle_keys()

    connection.send_acknowledgement()
    if not connection.has_message():
        BattleScreen.message(
            'Waiting for %s to finish ship placement...' % opponent_name
        )
    connection.wait_for_acknowledgement()

    message = '%s has finished ship placement. ' % opponent_name
    if player_starts:
        BattleScreen.message(message + 'Please take your first shot.')
    else:
        BattleScreen.message(message + 'Please wait for the first enemy shot.')

    return Fleet(ship_placements)


def _player_shot(connection, opponent_name, fleet):
    """
    lets the player shoot, receives the shot result from the opponent and
    displays it on screen.
    :param connection: the connection to the opponent's client
    :param opponent_name: the name of the opponent
    :param fleet: the fleet of the player
    """
    shot_coords = BattleScreen.let_player_shoot()
    shot_result = connection.deliver_shot(shot_coords)

    if shot_result.destroyed_ship:
        BattleScreen.reveal_ship(shot_result.destroyed_ship, True)
        if shot_result.game_over:
            connection.send_intact_ships(fleet)
            _check_for_rematch(connection, opponent_name, True)
            raise PlayAgain
        else:
            BattleScreen.message(
                "You destroyed a ship of size %d! It's %s's turn now..." %
                (len(shot_result.destroyed_ship), opponent_name)
            )
    else:
        BattleScreen.show_shot(
            shot_coords, shot_result.is_hit, opponent=True
        )
        if shot_result.is_hit:
            BattleScreen.message(
                "You scored a hit! It's %s's turn now..." % opponent_name
            )
        else:
            BattleScreen.message(
                "You missed! It's %s's turn now..." % opponent_name
            )


def _opponent_shot(connection, opponent_name, fleet):
    """
    receives the opponent's shot and displays the result.
    :param connection: the connection to the opponent's client
    :param opponent_name: the name of the opponent
    :param fleet: the fleet of the player
    """
    shot_coords = connection.receive_shot()
    is_hit = fleet.receive_shot(shot_coords)
    destroyed_ship = fleet.destroyed_ship  # is None if no ship got destroyed
    game_over = fleet.destroyed

    BattleScreen.show_shot(shot_coords, is_hit)
    connection.inform_shot_result(is_hit, game_over, destroyed_ship)

    if destroyed_ship:
        if game_over:
            BattleScreen.reveal_intact_ships(connection.enemy_intact_ships())
            _check_for_rematch(connection, opponent_name, False)
            raise PlayAgain
        else:
            BattleScreen.message(
                '%s has destroyed one of your ships! Take revenge!' %
                opponent_name
            )
    else:
        if is_hit:
            BattleScreen.message(
                '%s hit one of your ships! Shoot back!' % opponent_name
            )
        else:
            BattleScreen.message(
                "%s missed! Time to show %s how it's done!" %
                (opponent_name, opponent_name)
            )


def _check_for_rematch(connection, opponent_name, player_won):
    """
    asks the player whether he wants to play another game. if this is the case
    and the opponent also agrees to a rematch, this method simply returns. for
    other cases, an exception will be thrown.
    :param connection: the connection to the opponent's client
    :param opponent_name: the name of the opponent
    :param player_won: True if the player won the last battle, False otherwise
    """
    if player_won:
        message = 'Congratulations! You win!'
    else:
        message = '%s has destroyed your fleet! You lose!' % opponent_name

    if BattleScreen.ask_for_another_battle(message):
        connection.send_acknowledgement()
        if not connection.has_message():
            BattleScreen.message(
                'Waiting for the decision of %s to play again...' %
                opponent_name
            )
        connection.wait_for_acknowledgement()
        BattleScreen.message(
            "%s agreed to another battle! Please place your ships." %
            opponent_name
        )
    else:
        raise ProgramExit
