// Owner-built diagnostic. It does not load or modify the supplied game.
// Build with tools/game-support/build_gles_probe.py on macOS/Xcode.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <OpenGLES/EAGL.h>
#include <OpenGLES/ES2/gl.h>
#include <OpenGLES/ES2/glext.h>
#include <mach/mach_time.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <vector>

static NSString *glString(GLenum name) {
    const GLubyte *value = glGetString(name);
    NSString *text = value ? [NSString stringWithUTF8String:(const char *)value] : nil;
    return text ?: @"unavailable";
}

static uint64_t nanos(uint64_t ticks) {
    mach_timebase_info_data_t timebase;
    mach_timebase_info(&timebase);
    return (uint64_t)((long double)ticks * timebase.numer / timebase.denom);
}

static GLuint shader(GLenum kind, const char *source, NSMutableDictionary *report) {
    GLuint object = glCreateShader(kind);
    glShaderSource(object, 1, &source, nullptr);
    glCompileShader(object);
    GLint compiled = 0;
    glGetShaderiv(object, GL_COMPILE_STATUS, &compiled);
    if (!compiled) {
        char message[256] = {};
        glGetShaderInfoLog(object, sizeof(message)-1, nullptr, message);
        report[@"shader_error"] = [NSString stringWithUTF8String:message];
        glDeleteShader(object);
        return 0;
    }
    return object;
}

static bool drawTest(NSMutableDictionary *report) {
    GLuint texture = 0, framebuffer = 0, buffer = 0, program = 0;
    GLuint vertex = shader(GL_VERTEX_SHADER,
        "attribute vec2 position; void main(){gl_Position=vec4(position,0.0,1.0);}", report);
    GLuint fragment = shader(GL_FRAGMENT_SHADER,
        "precision mediump float; void main(){gl_FragColor=vec4(0.0,1.0,0.0,1.0);}", report);
    if (!vertex || !fragment) {
        if (vertex) glDeleteShader(vertex);
        if (fragment) glDeleteShader(fragment);
        return false;
    }
    program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glBindAttribLocation(program, 0, "position");
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    bool passed = false;
    if (linked) {
        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 16, 16, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        glGenFramebuffers(1, &framebuffer);
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
        GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
        report[@"framebuffer_status"] = @(status);
        if (status == GL_FRAMEBUFFER_COMPLETE) {
            const GLfloat vertices[] = {-1.f,-1.f, 3.f,-1.f, -1.f,3.f};
            glGenBuffers(1, &buffer);
            glBindBuffer(GL_ARRAY_BUFFER, buffer);
            glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
            glUseProgram(program);
            glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, nullptr);
            glEnableVertexAttribArray(0);
            glViewport(0, 0, 16, 16);
            glClearColor(0.f, 0.f, 0.f, 1.f);
            glClear(GL_COLOR_BUFFER_BIT);
            glDrawArrays(GL_TRIANGLES, 0, 3);
            GLubyte pixel[4] = {};
            glReadPixels(8, 8, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            GLenum error = glGetError();
            report[@"readback_rgba"] = @[@(pixel[0]), @(pixel[1]), @(pixel[2]), @(pixel[3])];
            report[@"draw_gl_error"] = @(error);
            passed = error == GL_NO_ERROR && pixel[0] <= 1 && pixel[1] >= 254 && pixel[2] <= 1 && pixel[3] >= 254;
            if (passed) {
                std::vector<uint64_t> samples;
                for (unsigned frame = 0; frame < 32; ++frame) {
                    uint64_t start = mach_absolute_time();
                    glDrawArrays(GL_TRIANGLES, 0, 3);
                    glFinish();
                    samples.push_back(nanos(mach_absolute_time()-start)/1000);
                }
                std::sort(samples.begin(), samples.end());
                report[@"offscreen_16x16_draw_finish_us_p50"] = @(samples[16]);
                report[@"offscreen_16x16_draw_finish_us_p95"] = @(samples[30]);
                report[@"timing_note"] = @"Offscreen microtest; not application FPS or touch latency";
            }
            glDisableVertexAttribArray(0);
        }
    } else {
        char message[256] = {};
        glGetProgramInfoLog(program, sizeof(message)-1, nullptr, message);
        report[@"link_error"] = [NSString stringWithUTF8String:message];
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glUseProgram(0);
    if (buffer) glDeleteBuffers(1, &buffer);
    if (framebuffer) glDeleteFramebuffers(1, &framebuffer);
    if (texture) glDeleteTextures(1, &texture);
    glDeleteProgram(program);
    glDeleteShader(vertex);
    glDeleteShader(fragment);
    return passed;
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        NSMutableDictionary *report = [NSMutableDictionary dictionary];
        report[@"probe"] = @"x910-gles-1";
        report[@"scope"] = @"System EAGL/GLES capability; supplied IPA remains unchanged";
        bool passed = false;
        @try {
            id<MTLDevice> metal = MTLCreateSystemDefaultDevice();
            report[@"metal_device_name"] = metal ? (metal.name ?: @"unnamed") : @"unavailable";
            EAGLContext *es1 = [[EAGLContext alloc] initWithAPI:kEAGLRenderingAPIOpenGLES1];
            EAGLContext *es3 = [[EAGLContext alloc] initWithAPI:kEAGLRenderingAPIOpenGLES3];
            report[@"es1_context"] = @(es1 != nil);
            report[@"es3_context"] = @(es3 != nil);
            EAGLContext *context = [[EAGLContext alloc] initWithAPI:kEAGLRenderingAPIOpenGLES2];
            if (context && [EAGLContext setCurrentContext:context]) {
                report[@"es2_context"] = @YES;
                report[@"gl_vendor"] = glString(GL_VENDOR);
                report[@"gl_renderer"] = glString(GL_RENDERER);
                report[@"gl_version"] = glString(GL_VERSION);
                NSString *extensions = [NSString stringWithFormat:@" %@ ", glString(GL_EXTENSIONS)];
                NSMutableDictionary *required = [NSMutableDictionary dictionary];
                for (NSString *name in @[@"GL_OES_vertex_array_object", @"GL_OES_mapbuffer",
                                         @"GL_EXT_discard_framebuffer", @"GL_APPLE_framebuffer_multisample"]) {
                    required[name] = @([extensions containsString:[NSString stringWithFormat:@" %@ ", name]]);
                }
                report[@"game_related_extensions"] = required;
                passed = drawTest(report);
                [EAGLContext setCurrentContext:nil];
            } else {
                report[@"es2_context"] = @NO;
                report[@"error"] = @"System EAGLContext creation/binding failed";
            }
        } @catch (NSException *exception) {
            report[@"exception"] = exception.name;
            NSString *reason = exception.reason ?: @"unavailable";
            report[@"exception_reason"] = [reason substringToIndex:MIN(reason.length, 256)];
        }
        report[@"draw_readback_passed"] = @(passed);
        NSData *json = [NSJSONSerialization dataWithJSONObject:report options:0 error:nullptr];
        NSString *body = [[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding];
        unsigned long long uptime = nanos(mach_absolute_time())/1000000000ULL;
        NSString *record = [NSString stringWithFormat:@"X910REPORT-BEGIN round=1500 uptime=%llu s gles-probe\n%@\nX910REPORT-END\n", uptime, body];
        NSString *path = argc > 1 ? [NSString stringWithUTF8String:argv[1]] : @"/private/var/db/x910-gles-probe.txt";
        NSError *error = nil;
        if ([record lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >= 4000 ||
            ![record writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:&error]) {
            fprintf(stderr, "Cannot persist GLES report: %s\n", error ? error.description.UTF8String : "report too large");
            return 3;
        }
        puts(record.UTF8String);
        return passed ? 0 : 2;
    }
}
