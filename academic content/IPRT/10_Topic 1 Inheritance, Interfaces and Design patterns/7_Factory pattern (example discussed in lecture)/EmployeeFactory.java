package empfactory;

public class EmployeeFactory {
	public Employee getType(char type, String id, String name, double amt) {
		Employee e=null;
		if((type=='P')||(type=='p')){
			e=new PartTime(type, id, name, (int)amt);
		}
		if((type=='F')||(type=='f')) {
			e=new FullTime(type, id, name, amt);
		}
		return e;
	}

}

