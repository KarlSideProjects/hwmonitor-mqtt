#define START "_start"
#include "entry.h"
typedef void (*fn)(void);
extern int main(int, char **, char **);
extern void _init(void);
extern fn __init_array_start[], __init_array_end[];
extern int __libc_start_main(int (*)(int,char **,char **), int, char **, void (*)(int,char **,char **), void (*)(void), void (*)(void), void *);
static void init(int argc, char **argv, char **env) {
    _init();
    for (fn *p = __init_array_start; p < __init_array_end; ++p) ((void (*)(int,char **,char **))*p)(argc,argv,env);
}
void _start_c(long *sp, void *dynamic, void (*rtld_fini)(void)) {
    (void)dynamic;
    __libc_start_main(main, (int)*sp, (char **)(sp+1), init, 0, rtld_fini, sp);
    __builtin_trap();
}
