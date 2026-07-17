from comfy_setup.profile import builtin_profiles_dir, default_profile, list_profiles


def test_builtin_profiles_are_inside_the_python_package_and_loadable() -> None:
    directory = builtin_profiles_dir()
    assert directory.name == "profiles"
    assert directory.parent.name == "comfy_setup"
    assert (directory / "vanilla.yaml").is_file()
    assert (directory / "badgids-complete.yaml").is_file()

    records = list_profiles()
    identifiers = {record.id for record in records}
    assert "vanilla-comfyui" in identifiers
    assert "badgids-comfyui-complete" in identifiers
    assert default_profile()["id"] == "vanilla-comfyui"


def test_builtin_profile_aliases() -> None:
    from comfy_setup.profile import get_profile

    assert get_profile("vanilla")["id"] == "vanilla-comfyui"
    assert get_profile("badgids")["id"] == "badgids-comfyui-complete"
