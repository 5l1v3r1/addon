# -*- coding: utf-8 -*-
# ------------------------------------------------------------
# XBMC Library Tools
# ------------------------------------------------------------
from future import standard_library
standard_library.install_aliases()
#from builtins import str
import sys, os, threading, time, re, math, xbmc, xbmcgui
PY3 = False
if sys.version_info[0] >= 3: PY3 = True; unicode = str; unichr = chr; long = int

from core import filetools, jsontools
from platformcode import config, logger, platformtools
from core import scrapertools
from xml.dom import minidom


def mark_auto_as_watched(item):
    def mark_as_watched_subThread(item, nfo_path, head_nfo, item_nfo):
        logger.info()
        # logger.debug("item:\n" + item.tostring('\n'))

        time_limit = time.time() + 30
        while not platformtools.is_playing() and time.time() < time_limit:
            time.sleep(1)

        sync_with_trakt = False
        percentage = float(config.get_setting("watched_setting")) / 100
        while platformtools.is_playing():
            actual_time = xbmc.Player().getTime()
            totaltime = xbmc.Player().getTotalTime()
            if item_nfo.played_time and actual_time < item_nfo.played_time:
                xbmc.Player().seekTime(item_nfo.played_time)

            mark_time = totaltime * percentage

            if actual_time > mark_time:
                logger.debug("Marked as Watched")
                item.playcount = 1
                sync_with_trakt = True
                from specials import videolibrary
                videolibrary.mark_content_as_watched2(item)
                break

            time.sleep(1)

        # Set played time
        if not sync_with_trakt and actual_time > 120: item_nfo.played_time = int(actual_time)
        else: item_nfo.played_time = 0
        filetools.write(nfo_path, head_nfo + item_nfo.tojson())

        # Silent sync with Trakt
        if sync_with_trakt and config.get_setting("trakt_sync"):sync_trakt_kodi()

    # If it is configured to mark as seen
    if config.get_setting("mark_as_watched", "videolibrary"):
        nfo_path, head_nfo, item_nfo = resume_playback(item)
        threading.Thread(target=mark_as_watched_subThread, args=[item, nfo_path, head_nfo, item_nfo]).start()


def resume_playback(item):
    class ResumePlayback(xbmcgui.WindowXMLDialog):
        Close = False
        Resume = False

        def __init__(self, *args, **kwargs):
            self.action_exitkeys_id = [xbmcgui.ACTION_BACKSPACE, xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK]
            self.progress_control = None
            self.item = kwargs.get('item')
            m, s = divmod(float(self.item.played_time), 60)
            h, m = divmod(m, 60)
            self.setProperty("title", config.get_localized_string(30045) +' %02d:%02d:%02d' % (h, m, s))

        def set_values(self, value):
            self.Resume = value
            self.Close = True

        def is_close(self):
            return self.Close

        def onClick(self, controlId):
            if controlId == 3012:  # Resume
                self.set_values(True)
                self.close()
            elif controlId == 3013:  # Cancel
                self.set_values(False)
                self.close()

        def onAction(self, action):
            if action in self.action_exitkeys_id:
                self.set_values(False)
                self.close()


    from core import videolibrarytools
    # from core.support import dbg;dbg()

    # if nfo and strm_path not exist
    if not item.nfo:
        if item.contentType == 'movie':
            vl = xbmc.translatePath(filetools.join(config.get_setting("videolibrarypath"), config.get_setting("folder_movies")))
            path = '%s [%s]' % (item.contentTitle, item.infoLabels['IMDBNumber'])
            item.nfo = filetools.join(vl, path, path + '.nfo')
            if not item.strm_path: item.strm_path = filetools.join(path, item.contentTitle + '.strm')
        else:
            vl = xbmc.translatePath(filetools.join(config.get_setting("videolibrarypath"), config.get_setting("folder_tvshows")))
            path = '%s [%s]' % (item.contentSerieName, item.infoLabels['IMDBNumber'])
            item.nfo = filetools.join(vl, path, 'tvshow.nfo')
            if item.contentSeason and item.contentEpisodeNumber:
                title = str(item.contentSeason) + 'x' + str(item.contentEpisodeNumber).zfill(2)
            else:
                season, episode = scrapertools.find_single_match(item.title, r'(\d+)x(\d+)')
                item.contentSeason = int(season)
                item.contentEpisodeNumber = int(episode)
                title = season + 'x' + episode.zfill(2)
            if not item.strm_path: item.strm_path = filetools.join(path, title + '.strm')

    # Read NFO FILE
    if item.contentType == 'movie':
        nfo_path = item.nfo
    elif xbmc.translatePath(filetools.join(config.get_setting("videolibrarypath"), config.get_setting("folder_tvshows"))) in item.strm_path:
        nfo_path = item.strm_path.replace('strm','nfo')
    else:
        nfo_path = xbmc.translatePath(filetools.join(config.get_setting("videolibrarypath"), config.get_setting("folder_tvshows"),item.strm_path.replace('strm','nfo')))
    head_nfo, item_nfo = videolibrarytools.read_nfo(nfo_path)

    # Show Window
    if (config.get_setting("player_mode") not in [3] or item.play_from == 'window') and item_nfo.played_time:
        Dialog = ResumePlayback('ResumePlayback.xml', config.get_runtime_path(), item=item_nfo)
        Dialog.show()
        t = 0
        while not Dialog.is_close() and t < 50:
            t += 1
            xbmc.sleep(100)
        if not Dialog.Resume: item_nfo.played_time = 0
    else:
        item_nfo.played_time = 0
    return nfo_path, head_nfo, item_nfo


def sync_trakt_addon(path_folder):
    """
       Updates the values ​​of episodes seen if
    """
    logger.info()
    # if the addon exists we do the search
    if xbmc.getCondVisibility('System.HasAddon("script.trakt")'):
        # we import dependencies
        paths = ["special://home/addons/script.module.dateutil/lib/", "special://home/addons/script.module.six/lib/",
                 "special://home/addons/script.module.arrow/lib/", "special://home/addons/script.module.trakt/lib/",
                 "special://home/addons/script.trakt/"]

        for path in paths:
            sys.path.append(xbmc.translatePath(path))

        # the series seen is obtained
        try:
            from resources.lib.traktapi import traktAPI
            traktapi = traktAPI()
        except:
            return

        shows = traktapi.getShowsWatched({})
        shows = list(shows.items())

        # we get the series id to compare
        _id = re.findall("\[(.*?)\]", path_folder, flags=re.DOTALL)[0]
        logger.debug("the id is %s" % _id)

        if "tt" in _id:
            type_id = "imdb"
        elif "tvdb_" in _id:
            _id = _id.strip("tvdb_")
            type_id = "tvdb"
        elif "tmdb_" in _id:
            type_id = "tmdb"
            _id = _id.strip("tmdb_")
        else:
            logger.error("There is no _id of the series")
            return

        # we obtain the values ​​of the series
        from core import videolibrarytools
        tvshow_file = filetools.join(path_folder, "tvshow.nfo")
        head_nfo, serie = videolibrarytools.read_nfo(tvshow_file)

        # we look in the trakt series
        for show in shows:
            show_aux = show[1].to_dict()

            try:
                _id_trakt = show_aux['ids'].get(type_id, None)
                # logger.debug("ID ES %s" % _id_trakt)
                if _id_trakt:
                    if _id == _id_trakt:
                        logger.debug("FOUND! %s" % show_aux)

                        # we create the trakt dictionary for the found series with the value that has "seen"
                        dict_trakt_show = {}

                        for idx_season, season in enumerate(show_aux['seasons']):
                            for idx_episode, episode in enumerate(show_aux['seasons'][idx_season]['episodes']):
                                sea_epi = "%sx%s" % (show_aux['seasons'][idx_season]['number'], str(show_aux['seasons'][idx_season]['episodes'][idx_episode]['number']).zfill(2))

                                dict_trakt_show[sea_epi] = show_aux['seasons'][idx_season]['episodes'][idx_episode]['watched']
                        logger.debug("dict_trakt_show %s " % dict_trakt_show)

                        # we get the keys that are episodes
                        regex_epi = re.compile('\d+x\d+')
                        keys_episodes = [key for key in serie.library_playcounts if regex_epi.match(key)]
                        # we get the keys that are seasons
                        keys_seasons = [key for key in serie.library_playcounts if 'season ' in key]
                        # we get the numbers of the seasons keys
                        seasons = [key.strip('season ') for key in keys_seasons]

                        # we mark the episodes watched
                        for k in keys_episodes:
                            serie.library_playcounts[k] = dict_trakt_show.get(k, 0)

                        for season in seasons:
                            episodios_temporada = 0
                            episodios_vistos_temporada = 0

                            # we obtain the keys of the episodes of a certain season
                            keys_season_episodes = [key for key in keys_episodes if key.startswith("%sx" % season)]

                            for k in keys_season_episodes:
                                episodios_temporada += 1
                                if serie.library_playcounts[k] > 0:
                                    episodios_vistos_temporada += 1

                            # it is verified that if all the episodes are watched, the season is marked as watched
                            if episodios_temporada == episodios_vistos_temporada:
                                serie.library_playcounts.update({"season %s" % season: 1})

                        temporada = 0
                        temporada_vista = 0

                        for k in keys_seasons:
                            temporada += 1
                            if serie.library_playcounts[k] > 0:
                                temporada_vista += 1

                        # sCheck that if all seasons are viewed, the series is marked as view
                        if temporada == temporada_vista:
                            serie.library_playcounts.update({serie.title: 1})

                        logger.debug("the new values %s " % serie.library_playcounts)
                        filetools.write(tvshow_file, head_nfo + serie.tojson())

                        break
                    else:
                        continue

                else:
                    logger.error("could not get id, trakt has: %s" % show_aux['ids'])

            except:
                import traceback
                logger.error(traceback.format_exc())


def sync_trakt_kodi(silent=True):
    # So that the synchronization is not silent it is worth with silent = False
    if xbmc.getCondVisibility('System.HasAddon("script.trakt")'):
        notificacion = True
        if (not config.get_setting("sync_trakt_notification", "videolibrary") and platformtools.is_playing()):
            notificacion = False

        xbmc.executebuiltin('RunScript(script.trakt,action=sync,silent=%s)' % silent)
        logger.info("Synchronization with Trakt started")

        if notificacion:
            platformtools.dialog_notification(config.get_localized_string(20000), config.get_localized_string(60045), time=2000)


def mark_content_as_watched_on_kodi(item, value=1):
    """
    mark the content as seen or not seen in the Kodi library
    @type item: item
    @param item: element to mark
    @type value: int
    @param value: > 0 for seen, 0 for not seen
    """
    logger.info()
    # logger.debug("item:\n" + item.tostring('\n'))
    payload_f = ''

    if item.contentType == "movie":
        movieid = 0
        payload = {"jsonrpc": "2.0", "method": "VideoLibrary.GetMovies",
                   "params": {"properties": ["title", "playcount", "originaltitle", "file"]},
                   "id": 1}

        data = get_data(payload)
        if 'result' in data and "movies" in data['result']:

            if item.strm_path:              # If Item is from an episode
                filename = filetools.basename(item.strm_path)
                head, tail = filetools.split(filetools.split(item.strm_path)[0])
            else:                           # If Item is from the Series
                filename = filetools.basename(item.path)
                head, tail = filetools.split(filetools.split(item.path)[0])
            path = filetools.join(tail, filename)

            for d in data['result']['movies']:
                if d['file'].replace("/", "\\").endswith(path.replace("/", "\\")):
                    # logger.debug("I mark the movie as a view")
                    movieid = d['movieid']
                    break

        if movieid != 0:
            payload_f = {"jsonrpc": "2.0", "method": "VideoLibrary.SetMovieDetails", "params": {"movieid": movieid, "playcount": value}, "id": 1}

    else:  # item.contentType != 'movie'
        episodeid = 0
        payload = {"jsonrpc": "2.0", "method": "VideoLibrary.GetEpisodes",
                   "params": {"properties": ["title", "playcount", "showtitle", "file", "tvshowid"]},
                   "id": 1}

        data = get_data(payload)
        if 'result' in data and "episodes" in data['result']:

            if item.strm_path:              # If Item is from an episode
                filename = filetools.basename(item.strm_path)
                head, tail = filetools.split(filetools.split(item.strm_path)[0])
            else:                           # If Item is from the Series
                filename = filetools.basename(item.path)
                head, tail = filetools.split(filetools.split(item.path)[0])
            path = filetools.join(tail, filename)

            for d in data['result']['episodes']:

                if d['file'].replace("/", "\\").endswith(path.replace("/", "\\")):
                    # logger.debug("I mark the episode as seen")
                    episodeid = d['episodeid']
                    break

        if episodeid != 0:
            payload_f = {"jsonrpc": "2.0", "method": "VideoLibrary.SetEpisodeDetails", "params": {"episodeid": episodeid, "playcount": value}, "id": 1}

    if payload_f:
        # Mark as seen
        data = get_data(payload_f)
        # logger.debug(str(data))
        if data['result'] != 'OK':
            logger.error("ERROR putting content as viewed")


def mark_season_as_watched_on_kodi(item, value=1):
    """
        mark the entire season as seen or unseen in the Kodi library
        @type item: item
        @param item: element to mark
        @type value: int
        @param value: > 0 for seen, 0 for not seen
        """
    logger.info()
    # logger.debug("item:\n" + item.tostring('\n'))

    # We can only mark the season as seen in the Kodi database if the database is local, in case of sharing database this functionality will not work
    if config.get_setting("db_mode", "videolibrary"):
        return

    if value == 0:
        value = 'Null'

    request_season = ''
    if item.contentSeason > -1:
        request_season = ' and c12= %s' % item.contentSeason

    tvshows_path = filetools.join(config.get_videolibrary_path(), config.get_setting("folder_tvshows"))
    item_path1 = "%" + item.path.replace("\\\\", "\\").replace(tvshows_path, "")
    if item_path1[:-1] != "\\":
        item_path1 += "\\"
    item_path2 = item_path1.replace("\\", "/")

    sql = 'update files set playCount= %s where idFile in (select idfile from episode_view where (strPath like "%s" or strPath like "%s")%s)' % (value, item_path1, item_path2, request_season)

    execute_sql_kodi(sql)


def mark_content_as_watched_on_kod(path):
    from specials import videolibrary
    from core import videolibrarytools

    """
        mark the entire series or movie as viewed or unseen in the Alpha Video Library based on their status in the Kodi Video Library
        @type str: path
        @param path: content folder to mark
        """
    logger.info()
    #logger.debug("path: " + path)

    FOLDER_MOVIES = config.get_setting("folder_movies")
    FOLDER_TVSHOWS = config.get_setting("folder_tvshows")
    VIDEOLIBRARY_PATH = config.get_videolibrary_config_path()
    if not VIDEOLIBRARY_PATH:
        return

    # We can only mark the content as a view in the Kodi database if the database is local, in case of sharing database this functionality will not work
    # if config.get_setting("db_mode", "videolibrary"):
    #    return

    path2 = ''
    if "special://" in VIDEOLIBRARY_PATH:
        if FOLDER_TVSHOWS in path:
            path2 = re. sub(r'.*?%s' % FOLDER_TVSHOWS, filetools.join(VIDEOLIBRARY_PATH, FOLDER_TVSHOWS), path).replace("\\", "/")
        if FOLDER_MOVIES in path:
            path2 = re. sub(r'.*?%s' % FOLDER_MOVIES, filetools.join(VIDEOLIBRARY_PATH, FOLDER_MOVIES), path).replace("\\", "/")

    if "\\" in path:
        path = path.replace("/", "\\")
    head_nfo, item = videolibrarytools.read_nfo(path)                   # I read the content .nfo
    if not item:
        logger.error('.NFO not found: ' + path)
        return

    if FOLDER_TVSHOWS in path:                                          # I check if it is CINEMA or SERIES
        contentType = "episode_view"                                    # I mark the Kodi Video BBDD table
        nfo_name = "tvshow.nfo"                                         # I build the name of the .nfo
        path1 = path.replace("\\\\", "\\").replace(nfo_name, '')        # for SQL I just need the folder
        if not path2:
            path2 = path1.replace("\\", "/")                            # Format no Windows
        else:
            path2 = path2.replace(nfo_name, '')

    else:
        contentType = "movie_view"                                      # I mark the Kodi Video BBDD table
        path1 = path.replace("\\\\", "\\")                              # Windows format
        if not path2:
            path2 = path1.replace("\\", "/")                            # Format no Windows
        nfo_name = scrapertools.find_single_match(path2, '\]\/(.*?)$')  # I build the name of the .nfo
        path1 = path1.replace(nfo_name, '')                             # for SQL I just need the folder
        path2 = path2.replace(nfo_name, '')                             # for SQL I just need the folder
    path2 = filetools.remove_smb_credential(path2)                      # If the file is on an SMB server, we remove the credentials

    # Let's execute the SQL statement
    sql = 'select strFileName, playCount from %s where (strPath like "%s" or strPath like "%s")' % (contentType, path1, path2)
    nun_records = 0
    records = None
    nun_records, records = execute_sql_kodi(sql)                        # SQL execution
    if nun_records == 0:                                                # is there an error?
        logger.error("SQL error: " + sql + ": 0 registros")
        return                                                          # we quit: either it is not cataloged in Kodi, or there is an error in the SQL

    for title, playCount in records:                                    # Now we go through all the records obtained
        if contentType == "episode_view":
            title_plain = title.replace('.strm', '')                    # If it is Serial, we remove the suffix .strm
        else:
            title_plain = scrapertools.find_single_match(item.strm_path, '.(.*?\s\[.*?\])') # if it's a movie, we remove the title
        if playCount is None or playCount == 0:                         # not yet seen, we set it to 0
            playCount_final = 0
        elif playCount >= 1:
            playCount_final = 1

        elif not PY3 and isinstance(title_plain, (str, unicode)):
            title_plain = title_plain.decode("utf-8").encode("utf-8")   # We do this because if it doesn't generate this: u'title_plain '
        elif PY3 and isinstance(title_plain, bytes):
            title_plain = title_plain.decode('utf-8')
        item.library_playcounts.update({title_plain: playCount_final})  # update the .nfo playCount

    if item.infoLabels['mediatype'] == "tvshow":                        # We update the Season and Series playCounts
        for season in item.library_playcounts:
            if "season" in season:                                      # we look for the tags "season" inside playCounts
                season_num = int(scrapertools.find_single_match(season, 'season (\d+)'))    # we save the season number
                item = videolibrary.check_season_playcount(item, season_num)    # We call the method that updates Temps. and series

    filetools.write(path, head_nfo + item.tojson())

    #logger.debug(item)


def get_data(payload):
    """
    get the information of the JSON-RPC call with the information passed in payload
    @type payload: dict
    @param payload: data
    :return:
    """
    import urllib.request, urllib.error
    logger.info("payload: %s" % payload)
    # Required header for XBMC JSON-RPC calls, otherwise you'll get a 415 HTTP response code - Unsupported media type
    headers = {'content-type': 'application/json'}

    if config.get_setting("db_mode", "videolibrary"):
        try:
            try:
                xbmc_port = config.get_setting("xbmc_puerto", "videolibrary")
            except:
                xbmc_port = 0

            xbmc_json_rpc_url = "http://" + config.get_setting("xbmc_host", "videolibrary") + ":" + str(xbmc_port) + "/jsonrpc"
            req = urllib.request.Request(xbmc_json_rpc_url, data=jsontools.dump(payload), headers=headers)
            f = urllib.request.urlopen(req)
            response = f.read()
            f.close()

            logger.info("get_data: response %s" % response)
            data = jsontools.load(response)
        except Exception as ex:
            template = "An exception of type %s occured. Arguments:\n%r"
            message = template % (type(ex).__name__, ex.args)
            logger.error("error en xbmc_json_rpc_url: %s" % message)
            data = ["error"]
    else:
        try:
            data = jsontools.load(xbmc.executeJSONRPC(jsontools.dump(payload)))
        except Exception as ex:
            template = "An exception of type %s occured. Arguments:\n%r"
            message = template % (type(ex).__name__, ex.args)
            logger.error("error en xbmc.executeJSONRPC: %s" % message)
            data = ["error"]

    logger.info("data: %s" % data)

    return data


def update(folder_content=config.get_setting("folder_tvshows"), folder=""):
    """
    Update the library depending on the type of content and the path passed to it.

    @type folder_content: str
    @param folder_content: type of content to update, series or movies
    @type folder: str
    @param folder: name of the folder to scan.
    """
    logger.info(folder)

    payload = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.Scan",
        "id": 1
    }

    if folder:
        folder = str(folder)
        videolibrarypath = config.get_videolibrary_config_path()

        if folder.endswith('/') or folder.endswith('\\'):
            folder = folder[:-1]

        update_path = None

        if videolibrarypath.startswith("special:"):
            if videolibrarypath.endswith('/'):
                videolibrarypath = videolibrarypath[:-1]
            update_path = videolibrarypath + "/" + folder_content + "/" + folder + "/"
        else:
            # update_path = filetools.join(videolibrarypath, folder_content, folder) + "/"   # Encoder problems in "folder"
            update_path = filetools.join(videolibrarypath, folder_content, ' ').rstrip()

        if videolibrarypath.startswith("special:") or not scrapertools.find_single_match(update_path, '(^\w+:\/\/)'):
            payload["params"] = {"directory": update_path}

    while xbmc.getCondVisibility('Library.IsScanningVideo()'):
        xbmc.sleep(500)

    data = get_data(payload)


def search_library_path():
    sql = 'SELECT strPath FROM path WHERE strPath LIKE "special://%/plugin.video.kod/library/" AND idParentPath ISNULL'
    nun_records, records = execute_sql_kodi(sql)
    if nun_records >= 1:
        logger.debug(records[0][0])
        return records[0][0]
    return None


def set_content(content_type, silent=False, custom=False):
    """
    Procedure to auto-configure the kodi video library with the default values
    @type content_type: str ('movie' o 'tvshow')
    @param content_type: content type to configure, series or movies
    """
    logger.info()
    continuar = True
    msg_text = ""
    videolibrarypath = config.get_setting("videolibrarypath")

    if content_type == 'movie':
        scraper = [config.get_localized_string(70093), config.get_localized_string(70096)]
        if not custom:
            seleccion = 0 # tmdb
        else:
            seleccion = platformtools.dialog_select(config.get_localized_string(70094), scraper)


        # Instalar The Movie Database
        if seleccion == -1 or seleccion == 0:
            if not xbmc.getCondVisibility('System.HasAddon(metadata.themoviedb.org)'):
                if not silent:
                    # Ask if we want to install metadata.themoviedb.org
                    install = platformtools.dialog_yesno(config.get_localized_string(60046))
                else:
                    install = True

                if install:
                    try:
                        # Install metadata.themoviedb.org
                        xbmc.executebuiltin('xbmc.installaddon(metadata.themoviedb.org)', True)
                        logger.info("Instalado el Scraper de películas de TheMovieDB")
                    except:
                        pass

                continuar = (install and xbmc.getCondVisibility('System.HasAddon(metadata.themoviedb.org)'))
                if not continuar:
                    msg_text = config.get_localized_string(60047)
            if continuar:
                xbmc.executebuiltin('Addon.OpenSettings(metadata.themoviedb.org)', True)

        # Instalar Universal Movie Scraper
        elif seleccion == 1:
            if continuar and not xbmc.getCondVisibility('System.HasAddon(metadata.universal)'):
                continuar = False
                if not silent:
                    # Ask if we want to install metadata.universal
                    install = platformtools.dialog_yesno(config.get_localized_string(70095))
                else:
                    install = True

                if install:
                    try:
                        xbmc.executebuiltin('xbmc.installaddon(metadata.universal)', True)
                        if xbmc.getCondVisibility('System.HasAddon(metadata.universal)'):
                            continuar = True
                    except:
                        pass

                continuar = (install and continuar)
                if not continuar:
                    msg_text = config.get_localized_string(70097)
            if continuar:
                xbmc.executebuiltin('Addon.OpenSettings(metadata.universal)', True)

    else:  # SERIES
        scraper = [config.get_localized_string(70098), config.get_localized_string(70093)]
        if not custom:
            seleccion = 0 # tvdb
        else:
            seleccion = platformtools.dialog_select(config.get_localized_string(70107), scraper)

        # Instalar The TVDB
        if seleccion == -1 or seleccion == 0:
            if not xbmc.getCondVisibility('System.HasAddon(metadata.tvdb.com)'):
                if not silent:
                    #Ask if we want to install metadata.tvdb.com
                    install = platformtools.dialog_yesno(config.get_localized_string(60048))
                else:
                    install = True

                if install:
                    try:
                        # Install metadata.tvdb.com
                        xbmc.executebuiltin('xbmc.installaddon(metadata.tvdb.com)', True)
                        logger.info("The TVDB series Scraper installed ")
                    except:
                        pass

                continuar = (install and xbmc.getCondVisibility('System.HasAddon(metadata.tvdb.com)'))
                if not continuar:
                    msg_text = config.get_localized_string(60049)
            if continuar:
                xbmc.executebuiltin('Addon.OpenSettings(metadata.tvdb.com)', True)

        # Instalar The Movie Database
        elif seleccion == 1:
            if continuar and not xbmc.getCondVisibility('System.HasAddon(metadata.tvshows.themoviedb.org)'):
                continuar = False
                if not silent:
                    # Ask if we want to install metadata.tvshows.themoviedb.org
                    install = platformtools.dialog_yesno(config.get_localized_string(60050))
                else:
                    install = True

                if install:
                    try:
                        # Install metadata.tvshows.themoviedb.org
                        xbmc.executebuiltin('xbmc.installaddon(metadata.tvshows.themoviedb.org)', True)
                        if xbmc.getCondVisibility('System.HasAddon(metadata.tvshows.themoviedb.org)'):
                            continuar = True
                    except:
                        pass

                continuar = (install and continuar)
                if not continuar:
                    msg_text = config.get_localized_string(60051)
            if continuar:
                xbmc.executebuiltin('Addon.OpenSettings(metadata.tvshows.themoviedb.org)', True)

    idPath = 0
    idParentPath = 0
    if continuar:
        continuar = False

        # We look for the idPath
        sql = 'SELECT MAX(idPath) FROM path'
        nun_records, records = execute_sql_kodi(sql)
        if nun_records == 1:
            idPath = records[0][0] + 1

        sql_videolibrarypath = videolibrarypath
        if sql_videolibrarypath.startswith("special://"):
            sql_videolibrarypath = sql_videolibrarypath.replace('/profile/', '/%/').replace('/home/userdata/', '/%/')
            sep = '/'
        elif scrapertools.find_single_match(sql_videolibrarypath, '(^\w+:\/\/)'):
            sep = '/'
        else:
            sep = os.sep

        if not sql_videolibrarypath.endswith(sep):
            sql_videolibrarypath += sep

        # We are looking for the idParentPath
        sql = 'SELECT idPath, strPath FROM path where strPath LIKE "%s"' % sql_videolibrarypath
        nun_records, records = execute_sql_kodi(sql)
        if nun_records == 1:
            idParentPath = records[0][0]
            videolibrarypath = records[0][1][:-1]
            continuar = True
        else:
            # There is no videolibrarypath in the DB: we insert it
            sql_videolibrarypath = videolibrarypath
            if not sql_videolibrarypath.endswith(sep):
                sql_videolibrarypath += sep

            sql = 'INSERT INTO path (idPath, strPath,  scanRecursive, useFolderNames, noUpdate, exclude) VALUES ' \
                  '(%s, "%s", 0, 0, 0, 0)' % (idPath, sql_videolibrarypath)
            nun_records, records = execute_sql_kodi(sql)
            if nun_records == 1:
                continuar = True
                idParentPath = idPath
                idPath += 1
            else:
                msg_text = config.get_localized_string(70101)

    if continuar:
        continuar = False

        # We set strContent, strScraper, scanRecursive and strSettings
        if content_type == 'movie':
            strContent = 'movies'
            scanRecursive = 2147483647
            if seleccion == -1 or seleccion == 0:
                strScraper = 'metadata.themoviedb.org'
                path_settings = xbmc.translatePath("special://profile/addon_data/metadata.themoviedb.org/settings.xml")
            elif seleccion == 1:
                strScraper = 'metadata.universal'
                path_settings = xbmc.translatePath("special://profile/addon_data/metadata.universal/settings.xml")
            if not os.path.exists(path_settings):
                logger.info("%s: %s" % (content_type, path_settings + " doesn't exist"))
                return continuar
            settings_data = filetools.read(path_settings)
            strSettings = ' '.join(settings_data.split()).replace("> <", "><")
            strSettings = strSettings.replace("\"","\'")
            strActualizar = "Do you want to set this Scraper in Spanish as the default option for movies?"
            if not videolibrarypath.endswith(sep):
                videolibrarypath += sep
            strPath = videolibrarypath + config.get_setting("folder_movies") + sep
        else:
            strContent = 'tvshows'
            scanRecursive = 0
            if seleccion == -1 or seleccion == 0:
                strScraper = 'metadata.tvdb.com'
                path_settings = xbmc.translatePath("special://profile/addon_data/metadata.tvdb.com/settings.xml")
            elif seleccion == 1:
                strScraper = 'metadata.tvshows.themoviedb.org'
                path_settings = xbmc.translatePath("special://profile/addon_data/metadata.tvshows.themoviedb.org/settings.xml")
            if not os.path.exists(path_settings):
                logger.info("%s: %s" % (content_type, path_settings + " doesn't exist"))
                return continuar
            settings_data = filetools.read(path_settings)
            strSettings = ' '.join(settings_data.split()).replace("> <", "><")
            strSettings = strSettings.replace("\"","\'")
            strActualizar = "Do you want to configure this Scraper in Spanish as a default option for series?"
            if not videolibrarypath.endswith(sep):
                videolibrarypath += sep
            strPath = videolibrarypath + config.get_setting("folder_tvshows") + sep

        logger.info("%s: %s" % (content_type, strPath))
        # We check if strPath already exists in the DB to avoid duplicates
        sql = 'SELECT idPath FROM path where strPath="%s"' % strPath
        nun_records, records = execute_sql_kodi(sql)
        sql = ""
        if nun_records == 0:
            # Insertamos el scraper
            sql = 'INSERT INTO path (idPath, strPath, strContent, strScraper, scanRecursive, useFolderNames, ' \
                  'strSettings, noUpdate, exclude, idParentPath) VALUES (%s, "%s", "%s", "%s", %s, 0, ' \
                  '"%s", 0, 0, %s)' % (
                      idPath, strPath, strContent, strScraper, scanRecursive, strSettings, idParentPath)
        else:
            if not silent:
                # Preguntar si queremos configurar themoviedb.org como opcion por defecto
                actualizar = platformtools.dialog_yesno(config.get_localized_string(70098), strActualizar)
            else:
                actualizar = True

            if actualizar:
                # Actualizamos el scraper
                idPath = records[0][0]
                sql = 'UPDATE path SET strContent="%s", strScraper="%s", scanRecursive=%s, strSettings="%s" ' \
                      'WHERE idPath=%s' % (strContent, strScraper, scanRecursive, strSettings, idPath)

        if sql:
            nun_records, records = execute_sql_kodi(sql)
            if nun_records == 1:
                continuar = True

        if not continuar:
            msg_text = config.get_localized_string(60055)

    if not continuar:
        heading = config.get_localized_string(70102) % content_type
    elif content_type == 'tvshow' and not xbmc.getCondVisibility(
            'System.HasAddon(metadata.tvshows.themoviedb.org)'):
        heading = config.get_localized_string(70103) % content_type
        msg_text = config.get_localized_string(60058)
    else:
        heading = config.get_localized_string(70103) % content_type
        msg_text = config.get_localized_string(70104)

    logger.info("%s: %s" % (heading, msg_text))
    return continuar


def update_db(old_path, new_path, old_movies_folder, new_movies_folder, old_tvshows_folder, new_tvshows_folder, progress):
    def path_replace(path, old, new):

        logger.info()
        logger.info('path: ' + path + ', old: ' + old + ', new: ' + new)

        if new.startswith("special://") or '://' in new: sep = '/'
        else: sep = os.sep

        path = path.replace(old,new)
        if sep == '/': path = path.replace('\\','/')
        else: path = path.replace('/','\\')

        return path

    logger.info()

    sql_old_path = old_path
    if sql_old_path.startswith("special://"):
        sql_old_path = sql_old_path.replace('/profile/', '/%/').replace('/home/userdata/', '/%/')
        sep = '/'
    elif '://' in sql_old_path:
        sep = '/'
    else: sep = os.sep
    if not sql_old_path.endswith(sep):
        sql_old_path += sep

    logger.info('sql_old_path: ' + sql_old_path)
    # search MAIN path in the DB
    sql = 'SELECT idPath, strPath FROM path where strPath LIKE "%s"' % sql_old_path
    logger.info('sql: ' + sql)
    nun_records, records = execute_sql_kodi(sql)

    # change main path
    if records:
        idPath = records[0][0]
        strPath = path_replace(records[0][1], old_path, new_path)
        sql = 'UPDATE path SET strPath="%s" WHERE idPath=%s' % (strPath, idPath)
        logger.info('sql: ' + sql)
        nun_records, records = execute_sql_kodi(sql)
    else:
        progress.update(100)
        xbmc.sleep(1000)
        progress.close()
        return

    p = 80
    progress.update(p, config.get_localized_string(20000), config.get_localized_string(80013))

    for OldFolder, NewFolder in [[old_movies_folder, new_movies_folder], [old_tvshows_folder, new_tvshows_folder]]:
        sql_old_folder = sql_old_path + OldFolder
        if not sql_old_folder.endswith(sep): sql_old_folder += sep

        # Search Main Sub Folder
        sql = 'SELECT idPath, strPath FROM path where strPath LIKE "%s"' % sql_old_folder
        logger.info('sql: ' + sql)
        nun_records, records = execute_sql_kodi(sql)

        # Change Main Sub Folder
        if records:
            for record in records:
                idPath = record[0]
                strPath = path_replace(record[1], filetools.join(old_path, OldFolder), filetools.join(new_path, NewFolder))
                sql = 'UPDATE path SET strPath="%s" WHERE idPath=%s' % (strPath, idPath)
                logger.info('sql: ' + sql)
                nun_records, records = execute_sql_kodi(sql)

        # Search if Sub Folder exixt in all paths
        sql_old_folder += '%'
        sql = 'SELECT idPath, strPath FROM path where strPath LIKE "%s"' % sql_old_folder
        logger.info('sql: ' + sql)
        nun_records, records = execute_sql_kodi(sql)

        #Change Sub Folder in all paths
        if records:
            for record in records:
                idPath = record[0]
                strPath = path_replace(record[1], filetools.join(old_path, OldFolder), filetools.join(new_path, NewFolder))
                sql = 'UPDATE path SET strPath="%s" WHERE idPath=%s' % (strPath, idPath)
                logger.info('sql: ' + sql)
                nun_records, records = execute_sql_kodi(sql)


        if OldFolder == old_movies_folder:
            # if is Movie Folder
            # search and modify in "movie"
            sql = 'SELECT idMovie, c22 FROM movie where c22 LIKE "%s"' % sql_old_folder
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            if records:
                for record in records:
                    idMovie = record[0]
                    strPath = path_replace(record[1], filetools.join(old_path, OldFolder), filetools.join(new_path, NewFolder))
                    sql = 'UPDATE movie SET c22="%s" WHERE idMovie=%s' % (strPath, idMovie)
                    logger.info('sql: ' + sql)
                    nun_records, records = execute_sql_kodi(sql)
        else:
            # if is TV Show Folder
            # search and modify in "episode"
            sql = 'SELECT idEpisode, c18 FROM episode where c18 LIKE "%s"' % sql_old_folder
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            if records:
                for record in records:
                    idEpisode = record[0]
                    strPath = path_replace(record[1], filetools.join(old_path, OldFolder), filetools.join(new_path, NewFolder))
                    sql = 'UPDATE episode SET c18="%s" WHERE idEpisode=%s' % (strPath, idEpisode)
                    logger.info('sql: ' + sql)
                    nun_records, records = execute_sql_kodi(sql)
        p += 5
        progress.update(p, config.get_localized_string(20000), config.get_localized_string(80013))

    progress.update(100)
    xbmc.sleep(1000)
    progress.close()
    xbmc.executebuiltin('XBMC.ReloadSkin()')


def clean(path_list=[]):
    def sql_format(path):
        if path.startswith("special://"):
            path = path.replace('/profile/', '/%/').replace('/home/userdata/', '/%/')
            sep = '/'
        elif '://' in path:
            sep = '/'
        else: sep = os.sep

        if sep == '/': path = path.replace('\\','/')
        else: path = path.replace('/','\\')

        return path, sep

    logger.info()

    progress = platformtools.dialog_progress_bg(config.get_localized_string(20000), config.get_localized_string(80025))
    progress.update(0)

    # if the path list is empty, clean the entire video library
    if not path_list:
        logger.info('the path list is empty, clean the entire video library')
        if not config.get_setting("videolibrary_kodi"):
            sql_path, sep = sql_format(config.get_setting("videolibrarypath"))
            if not sql_path.endswith(sep): sql_path += sep
            sql = 'SELECT idPath FROM path where strPath LIKE "%s"' % sql_path
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            idPath = records[0][0]
            sql = 'DELETE from path WHERE idPath=%s' % idPath
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            sql = 'DELETE from path WHERE idParentPath=%s' % idPath
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)

        from core import videolibrarytools
        for path, folders, files in filetools.walk(videolibrarytools.MOVIES_PATH):
            for folder in folders:
                path_list.append(filetools.join(config.get_setting("videolibrarypath"), videolibrarytools.FOLDER_MOVIES, folder))

        for path, folders, files in filetools.walk(videolibrarytools.TVSHOWS_PATH):
            for folder in folders:
                tvshow_nfo = filetools.join(path, folder, "tvshow.nfo")
                if filetools.exists(tvshow_nfo):
                    path_list.append(filetools.join(config.get_setting("videolibrarypath"), videolibrarytools.FOLDER_TVSHOWS, folder))

    logger.info('path_list: ' + str(path_list))
    if path_list: t = float(100) / len(path_list)
    for i, path in enumerate(path_list):
        progress.update(int(math.ceil((i + 1) * t)))

        if not path:
            continue

        sql_path, sep = sql_format(path)
        if filetools.isdir(path) and not sql_path.endswith(sep): sql_path += sep
        logger.info('path: ' + path)
        logger.info('sql_path: ' + sql_path)

        if filetools.isdir(path):
            # search movie in the DB
            sql = 'SELECT idMovie FROM movie where c22 LIKE "%s"' % (sql_path + '%')
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            # delete movie
            if records:
                payload = {"jsonrpc": "2.0", "method": "VideoLibrary.RemoveMovie", "id": 1, "params": {"movieid": records[0][0]}}
                data = get_data(payload)
                continue
            # search TV show in the DB
            sql = 'SELECT idShow FROM tvshow_view where strPath LIKE "%s"' % sql_path
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            # delete TV show
            if records:
                payload = {"jsonrpc": "2.0", "method": "VideoLibrary.RemoveTVShow", "id": 1, "params": {"tvshowid": records[0][0]}}
                data = get_data(payload)
        elif config.get_setting("folder_movies") in sql_path:
            # search movie in the DB
            sql = 'SELECT idMovie FROM movie where c22 LIKE "%s"' % sql_path
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            # delete movie
            if records:
                payload = {"jsonrpc": "2.0", "method": "VideoLibrary.RemoveMovie", "id": 1, "params": {"movieid": records[0][0]}}
                data = get_data(payload)
        else:
            # search episode in the DB
            sql = 'SELECT idEpisode FROM episode where c18 LIKE "%s"' % sql_path
            logger.info('sql: ' + sql)
            nun_records, records = execute_sql_kodi(sql)
            # delete episode
            if records:
                payload = {"jsonrpc": "2.0", "method": "VideoLibrary.RemoveEpisode", "id": 1, "params": {"episodeid": records[0][0]}}
                data = get_data(payload)

    progress.update(100)
    xbmc.sleep(1000)
    progress.close()


def execute_sql_kodi(sql):
    """
    Run sql query against kodi database
    @param sql: Valid sql query
    @type sql: str
    @return: Number of records modified or returned by the query
    @rtype nun_records: int
    @return: list with the query result
    @rtype records: list of tuples
    """
    logger.info()
    file_db = ""
    nun_records = 0
    records = None

    # We look for the archive of the video database according to the version of kodi
    video_db = config.get_platform(True)['video_db']
    if video_db:
        file_db = filetools.join(xbmc.translatePath("special://userdata/Database"), video_db)

    # alternative method to locate the database
    if not file_db or not filetools.exists(file_db):
        file_db = ""
        for f in filetools.listdir(xbmc.translatePath("special://userdata/Database")):
            path_f = filetools.join(xbmc.translatePath("special://userdata/Database"), f)

            if filetools.isfile(path_f) and f.lower().startswith('myvideos') and f.lower().endswith('.db'):
                file_db = path_f
                break

    if file_db:
        logger.info("DB file: %s" % file_db)
        conn = None
        try:
            import sqlite3
            conn = sqlite3.connect(file_db)
            cursor = conn.cursor()

            logger.info("Running sql: %s" % sql)
            cursor.execute(sql)
            conn.commit()

            records = cursor.fetchall()
            if sql.lower().startswith("select"):
                nun_records = len(records)
                if nun_records == 1 and records[0][0] is None:
                    nun_records = 0
                    records = []
            else:
                nun_records = conn.total_changes

            conn.close()
            logger.info("Query executed. Records: %s" % nun_records)

        except:
            logger.error("Error executing sql query")
            if conn:
                conn.close()

    else:
        logger.debug("Database not found")

    return nun_records, records


def check_sources(new_movies_path='', new_tvshows_path=''):
    def format_path(path):
        if path.startswith("special://") or '://' in path: sep = '/'
        else: sep = os.sep
        if not path.endswith(sep): path += sep
        return path

    logger.info()

    new_movies_path = format_path(new_movies_path)
    new_tvshows_path = format_path(new_tvshows_path)

    SOURCES_PATH = xbmc.translatePath("special://userdata/sources.xml")
    if filetools.isfile(SOURCES_PATH):
        xmldoc = minidom.parse(SOURCES_PATH)

        video_node = xmldoc.childNodes[0].getElementsByTagName("video")[0]
        paths_node = video_node.getElementsByTagName("path")
        list_path = [p.firstChild.data for p in paths_node]

        return new_movies_path in list_path, new_tvshows_path in list_path
    else:
        xmldoc = minidom.Document()
        source_nodes = xmldoc.createElement("sources")

        for type in ['programs', 'video', 'music', 'picture', 'files']:
            nodo_type = xmldoc.createElement(type)
            element_default = xmldoc.createElement("default")
            element_default.setAttribute("pathversion", "1")
            nodo_type.appendChild(element_default)
            source_nodes.appendChild(nodo_type)
        xmldoc.appendChild(source_nodes)

        return False, False


def update_sources(new='', old=''):
    logger.info()
    if new == old: return

    SOURCES_PATH = xbmc.translatePath("special://userdata/sources.xml")
    if filetools.isfile(SOURCES_PATH):
        xmldoc = minidom.parse(SOURCES_PATH)
    else:
        xmldoc = minidom.Document()
        source_nodes = xmldoc.createElement("sources")

        for type in ['programs', 'video', 'music', 'picture', 'files']:
            nodo_type = xmldoc.createElement(type)
            element_default = xmldoc.createElement("default")
            element_default.setAttribute("pathversion", "1")
            nodo_type.appendChild(element_default)
            source_nodes.appendChild(nodo_type)
        xmldoc.appendChild(source_nodes)

    # collect nodes
    # nodes = xmldoc.getElementsByTagName("video")
    video_node = xmldoc.childNodes[0].getElementsByTagName("video")[0]
    paths_node = video_node.getElementsByTagName("path")

    if old:
        # delete old path
        for node in paths_node:
            if node.firstChild.data == old:
                parent = node.parentNode
                remove = parent.parentNode
                remove.removeChild(parent)

        # write changes
        if sys.version_info[0] >= 3: #PY3
            filetools.write(SOURCES_PATH, '\n'.join([x for x in xmldoc.toprettyxml().splitlines() if x.strip()]))
        else:
            filetools.write(SOURCES_PATH, '\n'.join([x for x in xmldoc.toprettyxml().splitlines() if x.strip()]), vfs=False)
        logger.debug("The path %s has been removed from sources.xml" % old)

    if new:
        # create new path
        list_path = [p.firstChild.data for p in paths_node]
        if new in list_path:
            logger.info("The path %s already exists in sources.xml" % new)
            return
        logger.info("The path %s does not exist in sources.xml" % new)

        # if the path does not exist we create one
        source_node = xmldoc.createElement("source")

        # <name> Node
        name_node = xmldoc.createElement("name")
        sep = os.sep
        if new.startswith("special://") or scrapertools.find_single_match(new, r'(^\w+:\/\/)'):
            sep = "/"
        name = new
        if new.endswith(sep):
            name = new[:-1]
        name_node.appendChild(xmldoc.createTextNode(name.rsplit(sep)[-1]))
        source_node.appendChild(name_node)

        # <path> Node
        path_node = xmldoc.createElement("path")
        path_node.setAttribute("pathversion", "1")
        path_node.appendChild(xmldoc.createTextNode(new))
        source_node.appendChild(path_node)

        # <allowsharing> Node
        allowsharing_node = xmldoc.createElement("allowsharing")
        allowsharing_node.appendChild(xmldoc.createTextNode('true'))
        source_node.appendChild(allowsharing_node)

        # Añadimos <source>  a <video>
        video_node.appendChild(source_node)

        # write changes
        if sys.version_info[0] >= 3: #PY3
            filetools.write(SOURCES_PATH, '\n'.join([x for x in xmldoc.toprettyxml().splitlines() if x.strip()]))
        else:
            filetools.write(SOURCES_PATH, '\n'.join([x for x in xmldoc.toprettyxml().splitlines() if x.strip()]), vfs=False)
        logger.debug("The path %s has been added to sources.xml" % new)


def ask_set_content(silent=False):
    logger.info()
    logger.debug("videolibrary_kodi %s" % config.get_setting("videolibrary_kodi"))

    def do_config(custom=False):
        if set_content("movie", True, custom) and set_content("tvshow", True, custom):
            platformtools.dialog_ok(config.get_localized_string(80026), config.get_localized_string(70104))
            config.set_setting("videolibrary_kodi", True)
            from specials import videolibrary
            videolibrary.update_videolibrary()
            update()
        else:
            platformtools.dialog_ok(config.get_localized_string(80026), config.get_localized_string(80024))
            config.set_setting("videolibrary_kodi", False)

    # configuration during installation
    if not silent:
        # ask to configure Kodi video library
        if platformtools.dialog_yesno(config.get_localized_string(20000), config.get_localized_string(80015)):
            # ask for custom or default settings
            if not platformtools.dialog_yesno(config.get_localized_string(80026), config.get_localized_string(80016), "", "", config.get_localized_string(80017), config.get_localized_string(80018)):
                # input path and folders
                path = platformtools.dialog_browse(3, config.get_localized_string(80019), config.get_setting("videolibrarypath"))
                movies_folder = platformtools.dialog_input(config.get_setting("folder_movies"), config.get_localized_string(80020))
                tvshows_folder = platformtools.dialog_input(config.get_setting("folder_tvshows"), config.get_localized_string(80021))

                if path != "" and movies_folder != "" and tvshows_folder != "":
                    movies_path, tvshows_path = check_sources(filetools.join(path, movies_folder), filetools.join(path, tvshows_folder))
                    # configure later
                    if movies_path or tvshows_path:
                        platformtools.dialog_ok(config.get_localized_string(80026), config.get_localized_string(80029))
                    # set path and folders
                    else:
                        update_sources(path, config.get_setting("videolibrarypath"))
                        config.set_setting("videolibrarypath", path)
                        config.set_setting("folder_movies", movies_folder)
                        config.set_setting("folder_tvshows", tvshows_folder)
                        config.verify_directories_created()
                        do_config(True)
                # default path and folders
                else:
                    platformtools.dialog_ok(config.get_localized_string(80026), config.get_localized_string(80030))
                    do_config(True)
            # default settings
            else:
                platformtools.dialog_ok(config.get_localized_string(80026), config.get_localized_string(80027))
                do_config(False)
        # configure later
        else:
            platformtools.dialog_ok(config.get_localized_string(20000), config.get_localized_string(80022))
    # configuration from the settings menu
    else:
        platformtools.dialog_ok(config.get_localized_string(80026), config.get_localized_string(80023))
        do_config(True)