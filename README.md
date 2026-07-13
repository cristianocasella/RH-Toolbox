# RH-Toolbox
A set of scripts that can help dealing with Red Hat products.

## Disclaimer

These scripts are provided **as-is** without any warranty or guarantee. They are **not official Red Hat tools** and are **not supported by Red Hat** in any way. Use at your own risk. Always test in a non-production environment before deploying to production systems.

## Tools

### [oc-mirror Sync Automation](./oc-mirror-wrapper/)
Python wrapper for automating multi-stage oc-mirror workflows in disconnected OpenShift environments with support for unlimited target registries. Includes automatic working directory management, skopeo fallback for failed images, and comprehensive error handling.

### [ImageSet Update Checker](./imageset-check-update/)
Python utility to check for available updates in OpenShift ImageSetConfiguration files. Queries container registries (Docker Hub, Quay.io, Red Hat registries) to identify newer versions of images, operator catalogs, and platform releases before mirroring.

## Contributing

Contributions are welcome! If you have improvements, bug fixes, or new tools to add:
- Fork the repository
- Create a feature branch
- Submit a pull request with a clear description of your changes

Please ensure your contributions follow the same structure and documentation standards as existing tools.

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0) - see the [LICENSE](LICENSE) file for details.
