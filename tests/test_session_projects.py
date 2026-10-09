from devrag.session_projects import associate, repository_name


def test_repository_identity_requires_the_exact_github_owner():
    assert repository_name('git@github.com:Winkless213/demo-shop.git', 'Winkless213') == 'demo-shop'
    assert repository_name('https://github.com/Winkless213/demo-erp.git', 'Winkless213') == 'demo-erp'
    assert repository_name('https://example.com/Winkless213/demo-erp.git', 'Winkless213') is None
    assert repository_name('https://github.com/another/demo-erp.git', 'Winkless213') is None


def test_session_directory_mapping_requires_observed_git_evidence():
    sessions = [
        {'id': 'one', 'cwd': 'D:/legacy', 'git': {'repository_url': 'https://github.com/Winkless213/demo-erp.git'}},
        {'id': 'two', 'cwd': 'd:\\legacy', 'git': {}},
        {'id': 'three', 'cwd': 'D:/demo-erp', 'git': {}},
    ]
    mapped = associate(sessions, 'Winkless213', {'demo-erp'})
    assert mapped[0]['project'] == mapped[1]['project'] == 'demo-erp'
    assert mapped[0]['association'] == 'recorded_git_remote'
    assert mapped[1]['association'] == 'same_recorded_workspace'
    assert mapped[2]['project'] is None


def test_conflicting_workspace_and_foreign_remote_are_never_guessed():
    sessions = [
        {'id': 'one', 'cwd': 'D:/work', 'git': {'repository_url': 'https://github.com/Winkless213/one.git'}},
        {'id': 'two', 'cwd': 'D:/work', 'git': {'repository_url': 'https://github.com/Winkless213/two.git'}},
        {'id': 'three', 'cwd': 'D:/work', 'git': {}},
        {'id': 'four', 'cwd': 'D:/work', 'git': {'repository_url': 'https://github.com/other/one.git'}},
    ]
    mapped = associate(sessions, 'Winkless213', {'one', 'two'})
    assert mapped[2]['project'] is None
    assert mapped[3]['project'] is None
