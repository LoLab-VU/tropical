# from distutils.core import setup
from setuptools import setup, find_packages
from setuptools.extension import Extension
import sys
import os
import platform
from distutils.sysconfig import get_python_inc
import versioneer

try:
    import numpy
except ImportError:  # We do not have numpy installed
    build_requires = ['numpy>=1.14']
else:
    # If we're building a wheel, assume there already exist numpy wheels
    # for this platform, so it is safe to add numpy to build requirements.
    # See gh-5184.
    build_requires = (['numpy>=1.14'] if 'bdist_wheel' in sys.argv[1:]
                      else [])

try:
    from Cython.Distutils.build_ext import build_ext as _build_ext
except ImportError:
    from setuptools.command.build_ext import build_ext as _build_ext
    use_cython = False
else:
    use_cython = True


# factory function
def my_build_ext(pars):
    # import delayed:

    # include_dirs adjusted:
    class build_ext(_build_ext):
        def finalize_options(self):
            _build_ext.finalize_options(self)
            # Prevent numpy from thinking it is still in its setup process:
            __builtins__.__NUMPY_SETUP__ = False
            import numpy
            self.include_dirs.append(numpy.get_include())

    # object returned:
    return build_ext(pars)

#### libs
if platform.system() == "Windows":
    math_lib = []
else:
    math_lib = ['m']

#### Python include
py_inc = [get_python_inc()]

#### cmdclass
cmdclass = versioneer.get_cmdclass()

#### Extension modules
ext_modules = []
if use_cython:
    cmdclass.update({'build_ext': my_build_ext})
    ext_modules += [Extension("pydyno.lcs",
                              ["pydyno/lcs/clcs.c",
                               "pydyno/lcs/lcs.pyx"],
                              libraries=math_lib,
                              include_dirs=py_inc)]
else:
    cmdclass.update({'build_ext': my_build_ext})
    ext_modules += [Extension("pydyno.lcs",
                              ["pydyno/lcs/clcs.c",
                               "pydyno/lcs/lcs.c"],
                              libraries=math_lib,
                              include_dirs=py_inc)]

install_requires = ['pysb', 'matplotlib', 'anytree', 'scikit-learn', 'pydot', 'tqdm',
                    'editdistance', 'pandas', 'networkx', 'numpy', 'sympy==1.8']

this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, 'README.md'), 'r') as f:
    long_description = f.read()

setup(name='pydyno',
      version=versioneer.get_version(),
      description='Dynamic analysis of systems biology models',
      long_description=long_description,
      long_description_content_type='text/markdown',
      author='Oscar Ortega',
      author_email='oscar.ortega@vanderbilt.edu',
      packages=find_packages(),
      include_package_data=True,
      install_requires=install_requires,
      setup_requires=build_requires,
      tests_require=['pytest', 'pathos', 'hdbscan'],
      cmdclass=cmdclass,
      ext_modules=ext_modules,
      keywords=['systems', 'biology', 'model'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Environment :: Console',
          'Intended Audience :: Science/Research',
          'License :: OSI Approved :: MIT License',
          'Operating System :: OS Independent',
          'Programming Language :: Python :: 3',
          'Topic :: Scientific/Engineering :: Bio-Informatics',
          'Topic :: Scientific/Engineering :: Chemistry',
          'Topic :: Scientific/Engineering :: Mathematics',
      ],
      )
