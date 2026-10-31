def _myDetectVersion(cc):
    if isGccName(cc) or "clang" in cc:
        command = (
            cc,
            "-dumpversion",
            "-dumpfullversion",
        )
    else:
        command = (
            cc,
            "--version",
        )

    stdout, stderr, exit_code = executeProcess(command)

    if exit_code != 0:
        scons_details_logger.info(
            "Error, error exit from '%s' (%d) gave %r." % (command, exit_code, stderr)
        )
        return None

    line = stdout.splitlines()[0]

    if str is not bytes and type(line) is bytes:
        line = decodeData(line)

    line = line.strip()

    match = re.findall(r"[0-9]+(?:\.[0-9]+)+", line)
    if match:
        version = match[0]
    else:
        # gcc 8 or higher
        version = line.strip()

    version = tuple(int(part) for part in version.split("."))

    return version
