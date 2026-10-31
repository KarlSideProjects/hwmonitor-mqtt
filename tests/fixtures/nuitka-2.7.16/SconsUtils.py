def isGccName(cc_name):
    return (
        "gcc" in cc_name
        or "g++" in cc_name
        or "gnu-cc" in cc_name
        or "gnu-gcc" in cc_name
    )

def provideStaticSourceFile(env, sub_path, c11_mode):
    source_filename = os.path.join(env.nuitka_src, "static_src", sub_path)
    target_filename = os.path.join(
        env.source_dir, "static_src", os.path.basename(sub_path)
    )

    if hasFilenameExtension(target_filename, ".c") and not c11_mode:
        target_filename += "pp"  # .cpp suffix then.

    cheapCopyFile(source_filename, target_filename)

    return target_filename

def scanSourceDir(env, dirname, plugins):
    if not os.path.exists(dirname):
        return

    # If we use C11 capable compiler, all good. Otherwise use C++, which Scons
    # needs to derive from filenames, so make copies (or links) with a different
    # name.
    added_path = False

    for filename_base in sorted(os.listdir(dirname)):
        if filename_base.endswith(".h") and plugins and not added_path:
            # Adding path for source paths on the fly, spell-checker: ignore cpppath
            env.Append(CPPPATH=[dirname])
            added_path = True

        # Only C files are of interest here.
        if not hasFilenameExtension(
            filename_base, (".c", ".cpp")
        ) or not filename_base.startswith(("module.", "__", "plugin.")):
            continue

        filename = os.path.join(dirname, filename_base)

        target_filename = filename

        if isWin32Windows() and not isFilesystemEncodable(filename_base):
            target_filename = getWindowsShortPathName(target_filename)

            # Avoid ".C" suffixes, that MinGW64 wouldn't recognize.
            target_filename = changeFilenameExtension(
                target_filename, getFilenameExtension(target_filename).lower()
            )

        # We pretend to use C++ if no C11 compiler is present.
        if env.c11_mode:
            yield target_filename
        else:
            if hasFilenameExtension(filename, ".c"):
                target_filename += "pp"  # .cpp" suffix then

                os.rename(filename, target_filename)

            yield target_filename
