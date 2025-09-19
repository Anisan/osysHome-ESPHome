new Vue({
    el: '#esphome_devices',
    delimiters: ['[[', ']]'], // Измененные разделители
    data: {
        objects:[],
        links: undefined,
        sensor: undefined,
        devices: {},
        default_device:{
            name: '',
            host: '',
            port: '6053',
            password: '',
        },
        title_button:"Add",
        device: undefined,
        loading: true,

    },
    async created() {
        this.device = {...this.default_device}
        this.fetchObjects()
        await this.fetchDevices()
        this.connectSocket(); 
    },
    mounted() {

    },
    watch: {
    },
    computed: {
        objectOptions(){
            list = {}
            Object.keys(this.objects).forEach(key => {
                list[key] = this.objects[key].description
            });
            return list
        }
    },
    methods: {
        connectSocket() {
            this.socket = io(); // Подключаемся к серверу
            this.socket.emit('subscribeData',["ESPHome"]);
            this.socket.on('ESPHome', (data) => {
            if (data.operation == "sensor_update"){
                const updatedData = data.data
                console.log('Received updated sensor:', updatedData);
                // Обновляем данные в дереве
                const deviceName = updatedData['device']
                const sensorName = updatedData['sensor']
                const key = updatedData['key']
                const newStates = updatedData['state']
                  // Находим устройство по имени
                const deviceIndex = this.devices.findIndex(d => d.name === deviceName);
                if (deviceIndex === -1) {
                    console.error(`Устройство "${deviceName}" не найдено`);
                    return false;
                }
                
                const device = this.devices[deviceIndex];
                
                // Находим сенсор
                const sensorIndex = device.sensors.findIndex(s => 
                    s.name === sensorName
                );
                
                if (sensorIndex === -1) {
                    console.error(`Сенсор "${sensorName}" не найден в устройстве "${deviceName}"`);
                    return false;
                }
                
                // Обновляем состояние
                device.sensors[sensorIndex].state = newStates;

            }

            });
        },
        fetchObjects(){
                axios.get(`/api/object/list/details`)
                    .then(response => {
                        this.objects = response.data.result
                    })
                    .catch(error => {
                        console.log(error)
                        this.message = 'Error fetching: ' + error;
                    });
        },
        async fetchDevices() {
            this.loading = true
            try {
              const response = await axios.get('/api/ESPHome/devices');
              this.devices = response.data;
            } catch (error) {
              console.error("Error fetching devices:", error);
            }
            this.loading = false
        },
        addDevice(){
            this.device = {...this.default_device}
            $('#deviceModal').modal('show');
        },
        editDevice(device){
            try {
                this.device = {...device}
                console.log(this.device)
                $('#deviceModal').modal('show');
            } catch (error) {
                console.error("Error editing device:", error);
            }
        },
        async removeDevice(device){
            if (confirm('Are you sure you want to delete this device?')) {
                try {
                const response = await axios.delete('/api/ESPHome/device?id='+device.id);
                    console.log("Device removed successfully:", response.data);
                } catch (error) {
                console.error("Error delete device:", error);
                }
                this.fetchDevices()
            }
        },
        editSensors(device){
            try {
                this.device = {...device}
                console.log(this.device)
                $('#sensorsModal').modal('show');
            } catch (error) {
                console.error("Error editing sensors:", error);
            }
        },
        async saveDevice(){
            try {
                console.log(this.device)
              const response = await axios.post('/api/ESPHome/device', this.device);
              
            } catch (error) {
              console.error("Error save device:", error);
            }
            $('#deviceModal').modal('hide');
            $('#sensorsModal').modal('hide');
            this.fetchDevices()
        },
        editSensor(sensor){
            var states = {...sensor.state}
            for (let key of Object.keys(states)) {
                states[key] = ''
            }
            states = { ...states, ...sensor.links };
            var links = []
            for (let key of Object.keys(states)) {
                var op = states[key].split('.')
                if (op.length == 2){
                    links.push({
                        'name': key,
                        'object': op[0],
                        'property': op[1]
                    })
                }
                else
                    links.push({
                        'object': '',
                        'name': key,
                        'property': ''
                    })
            }           
            console.log(links)
            this.links = links
            this.sensor = sensor
            $('#sensorModal').modal('show');
        },
        saveLinks(){
            console.log(this.sensor, this.links)
            for (let link of this.links){
                if (link.object != ''){
                    var op = link.object + '.' + link.property
                    this.$set(this.sensor.links, link.name, op);
                }
                else
                    this.$set(this.sensor.links, link.name, '');
            }
            $('#sensorModal').modal('hide');
            console.log(this.sensor)
        }
    }
  });