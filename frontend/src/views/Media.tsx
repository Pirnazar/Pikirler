import { Header } from "../components/Header";
import { useApp } from "../app/context";
import { useI18n } from "../i18n";
import { MoviesView, cinemaText } from "./Movies";
import { YoutubeArchiveView } from "./YoutubeArchive";
import { MediaSearchView, mediaSearchText } from "./MediaSearch";

export function MediaView() {
  const { route } = useApp();
  const { t, lang } = useI18n();
  const movies = route.arg?.startsWith("movies") || false;
  const search = route.arg === "search";
  const c = cinemaText[lang];
  return <>
    <Header title={t("nav_media")} sub={search ? mediaSearchText[lang].subtitle : movies ? c.subtitle : t("archive_title")} />
    <nav className="pk-media-tabs" aria-label={t("nav_media")}>
      <a href="#/media" aria-current={!movies && !search ? "page" : undefined}>YouTube</a>
      <a href="#/media/movies" aria-current={movies ? "page" : undefined}>{c.cinema}</a>
      <a href="#/media/search" aria-current={search ? "page" : undefined}>{mediaSearchText[lang].tab}</a>
    </nav>
    {search ? <MediaSearchView /> : movies ? <MoviesView /> : <YoutubeArchiveView embedded />}
  </>;
}
