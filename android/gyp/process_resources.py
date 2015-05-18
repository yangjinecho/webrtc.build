#!/usr/bin/env python
#
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Process Android resources to generate R.java, and prepare for packaging.

This will crunch images and generate v14 compatible resources
(see generate_v14_compatible_resources.py).
"""

import codecs
import optparse
import os
import re
import shutil
import sys
import zipfile

import generate_v14_compatible_resources

from util import build_utils

# Import jinja2 from third_party/jinja2
sys.path.insert(1,
    os.path.join(os.path.dirname(__file__), '../../../third_party'))
from jinja2 import Template # pylint: disable=F0401


def ParseArgs(args):
  """Parses command line options.

  Returns:
    An options object as from optparse.OptionsParser.parse_args()
  """
  parser = optparse.OptionParser()
  build_utils.AddDepfileOption(parser)

  parser.add_option('--android-sdk', help='path to the Android SDK folder')
  parser.add_option('--android-sdk-tools',
                    help='path to the Android SDK build tools folder')
  parser.add_option('--non-constant-id', action='store_true')

  parser.add_option('--android-manifest', help='AndroidManifest.xml path')
  parser.add_option('--custom-package', help='Java package for R.java')
  parser.add_option(
      '--shared-resources',
      action='store_true',
      help='Make a resource package that can be loaded by a different'
      'application at runtime to access the package\'s resources.')

  parser.add_option('--resource-dirs',
                    help='Directories containing resources of this target.')
  parser.add_option('--dependencies-res-zips',
                    help='Resources from dependents.')

  parser.add_option('--resource-zip-out',
                    help='Path for output zipped resources.')

  parser.add_option('--R-dir',
                    help='directory to hold generated R.java.')
  parser.add_option('--srcjar-out',
                    help='Path to srcjar to contain generated R.java.')
  parser.add_option('--r-text-out',
                    help='Path to store the R.txt file generated by appt.')

  parser.add_option('--proguard-file',
                    help='Path to proguard.txt generated file')

  parser.add_option(
      '--v14-skip',
      action="store_true",
      help='Do not generate nor verify v14 resources')

  parser.add_option(
      '--extra-res-packages',
      help='Additional package names to generate R.java files for')
  parser.add_option(
      '--extra-r-text-files',
      help='For each additional package, the R.txt file should contain a '
      'list of resources to be included in the R.java file in the format '
      'generated by aapt')
  parser.add_option(
      '--include-all-resources',
      action='store_true',
      help='Include every resource ID in every generated R.java file '
      '(ignoring R.txt).')

  parser.add_option(
      '--all-resources-zip-out',
      help='Path for output of all resources. This includes resources in '
      'dependencies.')

  parser.add_option('--stamp', help='File to touch on success')

  (options, args) = parser.parse_args(args)

  if args:
    parser.error('No positional arguments should be given.')

  # Check that required options have been provided.
  required_options = (
      'android_sdk',
      'android_sdk_tools',
      'android_manifest',
      'dependencies_res_zips',
      'resource_dirs',
      'resource_zip_out',
      )
  build_utils.CheckOptions(options, parser, required=required_options)

  if (options.R_dir is None) == (options.srcjar_out is None):
    raise Exception('Exactly one of --R-dir or --srcjar-out must be specified.')

  return options


def CreateExtraRJavaFiles(
      r_dir, extra_packages, extra_r_text_files, shared_resources, include_all):
  if include_all:
    java_files = build_utils.FindInDirectory(r_dir, "R.java")
    if len(java_files) != 1:
      return
    r_java_file = java_files[0]
    r_java_contents = codecs.open(r_java_file, encoding='utf-8').read()

    for package in extra_packages:
      package_r_java_dir = os.path.join(r_dir, *package.split('.'))
      build_utils.MakeDirectory(package_r_java_dir)
      package_r_java_path = os.path.join(package_r_java_dir, 'R.java')
      new_r_java = re.sub(r'package [.\w]*;', u'package %s;' % package,
                          r_java_contents)
      codecs.open(package_r_java_path, 'w', encoding='utf-8').write(new_r_java)
  else:
    if len(extra_packages) != len(extra_r_text_files):
      raise Exception('Need one R.txt file per extra package')

    all_resources = {}
    r_txt_file = os.path.join(r_dir, 'R.txt')
    if not os.path.exists(r_txt_file):
      return
    with open(r_txt_file) as f:
      for line in f:
        m = re.match(r'(int(?:\[\])?) (\w+) (\w+) (.+)$', line)
        if not m:
          raise Exception('Unexpected line in R.txt: %s' % line)
        java_type, resource_type, name, value = m.groups()
        all_resources[(resource_type, name)] = (java_type, value)

    for package, r_text_file in zip(extra_packages, extra_r_text_files):
      if os.path.exists(r_text_file):
        package_r_java_dir = os.path.join(r_dir, *package.split('.'))
        build_utils.MakeDirectory(package_r_java_dir)
        package_r_java_path = os.path.join(package_r_java_dir, 'R.java')
        CreateExtraRJavaFile(
            package, package_r_java_path, r_text_file, all_resources,
            shared_resources)


def CreateExtraRJavaFile(
      package, r_java_path, r_text_file, all_resources, shared_resources):
  resources = {}
  with open(r_text_file) as f:
    for line in f:
      m = re.match(r'int(?:\[\])? (\w+) (\w+) ', line)
      if not m:
        raise Exception('Unexpected line in R.txt: %s' % line)
      resource_type, name = m.groups()
      java_type, value = all_resources[(resource_type, name)]
      if resource_type not in resources:
        resources[resource_type] = []
      resources[resource_type].append((name, java_type, value))

  template = Template("""/* AUTO-GENERATED FILE.  DO NOT MODIFY. */

package {{ package }};

public final class R {
    {% for resource_type in resources %}
    public static final class {{ resource_type }} {
        {% for name, java_type, value in resources[resource_type] %}
        {% if shared_resources %}
        public static {{ java_type }} {{ name }} = {{ value }};
        {% else %}
        public static final {{ java_type }} {{ name }} = {{ value }};
        {% endif %}
        {% endfor %}
    }
    {% endfor %}
    {% if shared_resources %}
    public static void onResourcesLoaded(int packageId) {
        {% for resource_type in resources %}
        {% for name, java_type, value in resources[resource_type] %}
        {% if java_type == 'int[]' %}
        for(int i = 0; i < {{ resource_type }}.{{ name }}.length; ++i) {
            {{ resource_type }}.{{ name }}[i] =
                    ({{ resource_type }}.{{ name }}[i] & 0x00ffffff)
                    | (packageId << 24);
        }
        {% else %}
        {{ resource_type }}.{{ name }} =
                ({{ resource_type }}.{{ name }} & 0x00ffffff)
                | (packageId << 24);
        {% endif %}
        {% endfor %}
        {% endfor %}
    }
    {% endif %}
}
""", trim_blocks=True, lstrip_blocks=True)

  output = template.render(package=package, resources=resources,
                           shared_resources=shared_resources)
  with open(r_java_path, 'w') as f:
    f.write(output)


def CrunchDirectory(aapt, input_dir, output_dir):
  """Crunches the images in input_dir and its subdirectories into output_dir.

  If an image is already optimized, crunching often increases image size. In
  this case, the crunched image is overwritten with the original image.
  """
  aapt_cmd = [aapt,
              'crunch',
              '-C', output_dir,
              '-S', input_dir,
              '--ignore-assets', build_utils.AAPT_IGNORE_PATTERN]
  build_utils.CheckOutput(aapt_cmd, stderr_filter=FilterCrunchStderr,
                          fail_func=DidCrunchFail)

  # Check for images whose size increased during crunching and replace them
  # with their originals (except for 9-patches, which must be crunched).
  for dir_, _, files in os.walk(output_dir):
    for crunched in files:
      if crunched.endswith('.9.png'):
        continue
      if not crunched.endswith('.png'):
        raise Exception('Unexpected file in crunched dir: ' + crunched)
      crunched = os.path.join(dir_, crunched)
      original = os.path.join(input_dir, os.path.relpath(crunched, output_dir))
      original_size = os.path.getsize(original)
      crunched_size = os.path.getsize(crunched)
      if original_size < crunched_size:
        shutil.copyfile(original, crunched)


def FilterCrunchStderr(stderr):
  """Filters out lines from aapt crunch's stderr that can safely be ignored."""
  filtered_lines = []
  for line in stderr.splitlines(True):
    # Ignore this libpng warning, which is a known non-error condition.
    # http://crbug.com/364355
    if ('libpng warning: iCCP: Not recognizing known sRGB profile that has '
        + 'been edited' in line):
      continue
    filtered_lines.append(line)
  return ''.join(filtered_lines)


def DidCrunchFail(returncode, stderr):
  """Determines whether aapt crunch failed from its return code and output.

  Because aapt's return code cannot be trusted, any output to stderr is
  an indication that aapt has failed (http://crbug.com/314885).
  """
  return returncode != 0 or stderr


def ZipResources(resource_dirs, zip_path):
  # Python zipfile does not provide a way to replace a file (it just writes
  # another file with the same name). So, first collect all the files to put
  # in the zip (with proper overriding), and then zip them.
  files_to_zip = dict()
  for d in resource_dirs:
    for root, _, files in os.walk(d):
      for f in files:
        archive_path = os.path.join(os.path.relpath(root, d), f)
        path = os.path.join(root, f)
        files_to_zip[archive_path] = path
  with zipfile.ZipFile(zip_path, 'w') as outzip:
    for archive_path, path in files_to_zip.iteritems():
      outzip.write(path, archive_path)


def CombineZips(zip_files, output_path):
  # When packaging resources, if the top-level directories in the zip file are
  # of the form 0, 1, ..., then each subdirectory will be passed to aapt as a
  # resources directory. While some resources just clobber others (image files,
  # etc), other resources (particularly .xml files) need to be more
  # intelligently merged. That merging is left up to aapt.
  with zipfile.ZipFile(output_path, 'w') as outzip:
    for i, z in enumerate(zip_files):
      with zipfile.ZipFile(z, 'r') as inzip:
        for name in inzip.namelist():
          new_name = '%d/%s' % (i, name)
          outzip.writestr(new_name, inzip.read(name))


def main():
  args = build_utils.ExpandFileArgs(sys.argv[1:])

  options = ParseArgs(args)
  android_jar = os.path.join(options.android_sdk, 'android.jar')
  aapt = os.path.join(options.android_sdk_tools, 'aapt')

  input_files = []

  with build_utils.TempDir() as temp_dir:
    deps_dir = os.path.join(temp_dir, 'deps')
    build_utils.MakeDirectory(deps_dir)
    v14_dir = os.path.join(temp_dir, 'v14')
    build_utils.MakeDirectory(v14_dir)

    gen_dir = os.path.join(temp_dir, 'gen')
    build_utils.MakeDirectory(gen_dir)

    input_resource_dirs = build_utils.ParseGypList(options.resource_dirs)

    if not options.v14_skip:
      for resource_dir in input_resource_dirs:
        generate_v14_compatible_resources.GenerateV14Resources(
            resource_dir,
            v14_dir)

    dep_zips = build_utils.ParseGypList(options.dependencies_res_zips)
    input_files += dep_zips
    dep_subdirs = []
    for z in dep_zips:
      subdir = os.path.join(deps_dir, os.path.basename(z))
      if os.path.exists(subdir):
        raise Exception('Resource zip name conflict: ' + os.path.basename(z))
      build_utils.ExtractAll(z, path=subdir)
      dep_subdirs.append(subdir)

    # Generate R.java. This R.java contains non-final constants and is used only
    # while compiling the library jar (e.g. chromium_content.jar). When building
    # an apk, a new R.java file with the correct resource -> ID mappings will be
    # generated by merging the resources from all libraries and the main apk
    # project.
    package_command = [aapt,
                       'package',
                       '-m',
                       '-M', options.android_manifest,
                       '--auto-add-overlay',
                       '-I', android_jar,
                       '--output-text-symbols', gen_dir,
                       '-J', gen_dir,
                       '--ignore-assets', build_utils.AAPT_IGNORE_PATTERN]

    for d in input_resource_dirs:
      package_command += ['-S', d]

    for d in dep_subdirs:
      package_command += ['-S', d]

    if options.non_constant_id:
      package_command.append('--non-constant-id')
    if options.custom_package:
      package_command += ['--custom-package', options.custom_package]
    if options.proguard_file:
      package_command += ['-G', options.proguard_file]
    if options.shared_resources:
      package_command.append('--shared-lib')
    build_utils.CheckOutput(package_command, print_stderr=False)

    if options.extra_res_packages:
      CreateExtraRJavaFiles(
          gen_dir,
          build_utils.ParseGypList(options.extra_res_packages),
          build_utils.ParseGypList(options.extra_r_text_files),
          options.shared_resources,
          options.include_all_resources)

    # This is the list of directories with resources to put in the final .zip
    # file. The order of these is important so that crunched/v14 resources
    # override the normal ones.
    zip_resource_dirs = input_resource_dirs + [v14_dir]

    base_crunch_dir = os.path.join(temp_dir, 'crunch')

    # Crunch image resources. This shrinks png files and is necessary for
    # 9-patch images to display correctly. 'aapt crunch' accepts only a single
    # directory at a time and deletes everything in the output directory.
    for idx, input_dir in enumerate(input_resource_dirs):
      crunch_dir = os.path.join(base_crunch_dir, str(idx))
      build_utils.MakeDirectory(crunch_dir)
      zip_resource_dirs.append(crunch_dir)
      CrunchDirectory(aapt, input_dir, crunch_dir)

    ZipResources(zip_resource_dirs, options.resource_zip_out)

    if options.all_resources_zip_out:
      CombineZips([options.resource_zip_out] + dep_zips,
                  options.all_resources_zip_out)

    if options.R_dir:
      build_utils.DeleteDirectory(options.R_dir)
      shutil.copytree(gen_dir, options.R_dir)
    else:
      build_utils.ZipDir(options.srcjar_out, gen_dir)

    if options.r_text_out:
      r_text_path = os.path.join(gen_dir, 'R.txt')
      if os.path.exists(r_text_path):
        shutil.copyfile(r_text_path, options.r_text_out)
      else:
        open(options.r_text_out, 'w').close()

  if options.depfile:
    input_files += build_utils.GetPythonDependencies()
    build_utils.WriteDepfile(options.depfile, input_files)

  if options.stamp:
    build_utils.Touch(options.stamp)


if __name__ == '__main__':
  main()
