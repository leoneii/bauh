from argparse import Namespace
from threading import Thread
from typing import List, Dict

from bauh_api.abstract.controller import ApplicationManager
from bauh_api.abstract.handler import ProcessHandler
from bauh_api.abstract.model import Application, ApplicationUpdate
from bauh_api.util.disk import DiskCacheLoader
from bauh_api.util.disk import DiskCacheLoaderFactory
from bauh_api.util.system import SystemProcess

from bauh import ROOT_DIR

SUGGESTIONS_LIMIT = 6


class GenericApplicationManager(ApplicationManager):

    def __init__(self, managers: List[ApplicationManager], disk_loader_factory: DiskCacheLoaderFactory, app_args: Namespace, locale_keys: dict):
        super(GenericApplicationManager, self).__init__(app_args=app_args, app_cache=None, locale_keys=locale_keys, app_root_dir=ROOT_DIR, http_client=None)
        self.managers = managers
        self.map = {m.get_app_type(): m for m in self.managers}
        self.disk_loader_factory = disk_loader_factory
        self._enabled_map = {} if app_args.check_packaging_once else None
        self.thread_prepare = None

    def _sort(self, apps: List[Application], word: str) -> List[Application]:

        exact_name_matches, contains_name_matches, others = [], [], []

        for app in apps:
            lower_name = app.base_data.name.lower()

            if word == lower_name:
                exact_name_matches.append(app)
            elif word in lower_name:
                contains_name_matches.append(app)
            else:
                others.append(app)

        res = []
        for app_list in (exact_name_matches, contains_name_matches, others):
            app_list.sort(key=lambda a: a.base_data.name.lower())
            res.extend(app_list)

        return res

    def _is_enabled(self, man: ApplicationManager):

        if self._enabled_map is not None:
            enabled = self._enabled_map.get(man.get_app_type())

            if enabled is None:
                enabled = man.is_enabled()
                self._enabled_map[man.get_app_type()] = enabled

            return enabled
        else:
            return man.is_enabled()

    def _search(self, word: str, man: ApplicationManager, disk_loader, res: dict):
        if self._is_enabled(man):
            apps_found = man.search(words=word, disk_loader=disk_loader)
            res['installed'].extend(apps_found['installed'])
            res['new'].extend(apps_found['new'])

    def search(self, word: str, disk_loader: DiskCacheLoader = None) -> Dict[str, List[Application]]:
        self._wait_to_be_ready()

        res = {'installed': [], 'new': []}

        norm_word = word.strip().lower()
        disk_loader = self.disk_loader_factory.new()
        disk_loader.start()

        threads = []

        for man in self.managers:
            t = Thread(target=self._search, args=(norm_word, man, disk_loader, res))
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        if disk_loader:
            disk_loader.stop = True
            disk_loader.join()

        for key in res:
            res[key] = self._sort(res[key], norm_word)

        return res

    def _wait_to_be_ready(self):
        if self.thread_prepare:
            self.thread_prepare.join()
            self.thread_prepare = None

    def read_installed(self, disk_loader: DiskCacheLoader = None) -> List[Application]:
        self._wait_to_be_ready()

        installed = []

        disk_loader = None

        for man in self.managers:
            if self._is_enabled(man):
                if not disk_loader:
                    disk_loader = self.disk_loader_factory.new()
                    disk_loader.start()

                installed.extend(man.read_installed(disk_loader=disk_loader))

        if disk_loader:
            disk_loader.stop = True
            disk_loader.join()

        installed.sort(key=lambda a: a.base_data.name.lower())

        return installed

    def downgrade_app(self, app: Application, root_password: str, handler: ProcessHandler) -> bool:
        man = self._get_manager_for(app)

        if man and app.can_be_downgraded():
            return man.downgrade_app(app, root_password, handler)
        else:
            raise Exception("downgrade is not possible for {}".format(app.__class__.__name__))

    def clean_cache_for(self, app: Application):
        man = self._get_manager_for(app)

        if man:
            return man.clean_cache_for(app)

    def update(self, app: Application, root_password: str, handler: ProcessHandler) -> bool:
        man = self._get_manager_for(app)

        if man:
            return man.update(app, root_password, handler)

    def uninstall(self, app: Application, root_password: str, handler: ProcessHandler) -> bool:
        man = self._get_manager_for(app)

        if man:
            return man.uninstall(app, root_password, handler)

    def install(self, app: Application, root_password: str, handler: ProcessHandler) -> bool:
        man = self._get_manager_for(app)

        if man:
            return man.install(app, root_password, handler)

    def get_info(self, app: Application):
        man = self._get_manager_for(app)

        if man:
            return man.get_info(app)

    def get_history(self, app: Application):
        man = self._get_manager_for(app)

        if man:
            return man.get_history(app)

    def get_app_type(self):
        return None

    def is_enabled(self):
        return True

    def _get_manager_for(self, app: Application) -> ApplicationManager:
        man = self.map[app.__class__]
        return man if man and self._is_enabled(man) else None

    def cache_to_disk(self, app: Application, icon_bytes: bytes, only_icon: bool):
        if self.disk_loader_factory.disk_cache and app.supports_disk_cache():
            man = self._get_manager_for(app)

            if man:
                return man.cache_to_disk(app, icon_bytes=icon_bytes, only_icon=only_icon)

    def requires_root(self, action: str, app: Application):
        man = self._get_manager_for(app)

        if man:
            return man.requires_root(action, app)

    def refresh(self, app: Application, root_password: str) -> SystemProcess:
        self._wait_to_be_ready()

        man = self._get_manager_for(app)

        if man:
            return man.refresh(app, root_password)

    def _prepare(self):
        if self.managers:
            for man in self.managers:
                if self._is_enabled(man):
                    man.prepare()

    def prepare(self):
        self.thread_prepare = Thread(target=self._prepare)
        self.thread_prepare.start()

    def list_updates(self) -> List[ApplicationUpdate]:
        self._wait_to_be_ready()

        updates = []

        if self.managers:
            for man in self.managers:
                if self._is_enabled(man):
                    man_updates = man.list_updates()
                    if man_updates:
                        updates.extend(man_updates)

        return updates

    def list_warnings(self) -> List[str]:
        warnings = []

        if self.managers:
            for man in self.managers:
                man_warnings = man.list_warnings()

                if man_warnings:
                    if warnings is None:
                        warnings = []

                    warnings.extend(man_warnings)
        else:
            warnings.append(self.locale_keys['warning.no_managers'])

        return warnings

    def _fill_suggestions(self, suggestions: list, man: ApplicationManager, limit: int):
        if self._is_enabled(man):
            man_sugs = man.list_suggestions(limit)

            if man_sugs:
                suggestions.extend(man_sugs)

    def list_suggestions(self, limit: int) -> List[Application]:
        if self.managers:
            suggestions, threads = [], []
            for man in self.managers:
                t = Thread(target=self._fill_suggestions, args=(suggestions, man, SUGGESTIONS_LIMIT))
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

            return suggestions
