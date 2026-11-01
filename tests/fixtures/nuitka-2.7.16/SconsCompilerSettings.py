if "force-c11-mode" in env.experimental_flags:
    c11_mode = True
elif "force-cpp-mode" in env.experimental_flags:
    c11_mode = False
elif env.clangcl_mode:
    c11_mode = True
elif (
    env.msvc_mode
    and env.windows_sdk_version >= _windows_sdk_c11_mode_min_version
    and getMsvcVersion(env) >= (14, 3)
):
    c11_mode = True
elif env.clang_mode:
    c11_mode = True

    # For now, zig doesn't support C11 mode in the form needed by Nuitka
    if isZigName(env.the_cc_name):
        c11_mode = False
elif env.gcc_mode and env.gcc_version >= (5,):
    c11_mode = True
else:
    c11_mode = False
