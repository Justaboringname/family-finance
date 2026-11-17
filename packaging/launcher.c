// 「家庭资产.app」的主程序：只做一件事——用系统自带的 /bin/bash 运行 Contents/Resources/launch.sh。
// 用编译好的小程序当主程序（而不是直接用脚本），代码签名才能嵌在文件里，经压缩包、微信传过去不会丢。
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    char exe[PATH_MAX], real[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) {
        return 1;
    }
    // .../Contents/MacOS/launcher → .../Contents/Resources/launch.sh
    snprintf(script, sizeof script, "%s/../Resources/launch.sh", dirname(real));
    execl("/bin/bash", "bash", script, (char *)NULL);
    perror("execl /bin/bash");
    return 1;
}
