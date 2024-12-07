import tempfile

from quodlibet import app
from quodlibet import _
from quodlibet.qltk.notif import Task
from quodlibet.qltk import Icons
from quodlibet.util import copool

from quodlibet.plugins.songsmenu import SongsMenuPlugin
from quodlibet.util.i18n import numeric_phrase

from beets.ui import main as beets


class DownloadLyrics(SongsMenuPlugin):
    PLUGIN_ID = "download_lyrics"
    PLUGIN_NAME = _("Download lyrics")
    PLUGIN_DESC = _("Download lyrics of selected songs")
    PLUGIN_ICON = Icons.EMBLEM_DOWNLOADS

    def plugin_songs(self, songs):
        def download_lyrics():
            desc = numeric_phrase("%d song", "%d songs", len(songs))
            with Task(_("Download lyrics"), desc) as task:
                task.copool(download_lyrics)
                with tempfile.NamedTemporaryFile() as fp:
                    song_filenames = [s._song["~filename"] for s in songs]

                    # Import selected songs to a temporary beets library
                    beets(
                        [
                            f"--library={fp.name}",
                            "import",
                            "--quiet",
                            "--nocopy",
                            "--noautotag",
                            "--singletons",
                        ]
                        + song_filenames
                    )

                    # Download lyrics of selected songs
                    for i, song in enumerate(songs):
                        beets(
                            [
                                "--plugins=lyrics",
                                "lyrics",
                                "--force",
                                song._song["title"],
                            ]
                        )
                        app.library.reload(song._song)
                        task.update((float(i) + 1) / len(songs))
                        yield

        copool.add(download_lyrics)
